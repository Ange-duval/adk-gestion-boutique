from odoo import Command
from odoo.exceptions import UserError
from odoo.tests import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestAdkPos(TransactionCase):
    """Caisse intégrée : ouverture de journée, ventes comptoir, clôture."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env.company
        cls.has_chart = bool(cls.env['account.journal'].search([('type', '=', 'sale'), ('company_id', '=', cls.company.id)], limit=1))
        cls.warehouse = cls.env['stock.warehouse'].search([('company_id', '=', cls.company.id)], limit=1)
        cls.shop = cls.env['adk.shop'].create({
            'name': 'Boutique Caisse', 'code': 'CAISSE', 'state': 'active', 'warehouse_id': cls.warehouse.id,
            'sale_mode': 'both'})
        cls.product = cls.env['product.product'].create({
            'name': 'Robe test', 'is_storable': True, 'list_price': 100.0, 'standard_price': 40.0,
            'taxes_id': [Command.clear()], 'supplier_taxes_id': [Command.clear()]})
        cls.env['stock.quant']._update_available_quantity(cls.product, cls.warehouse.lot_stock_id, 20.0)
        cls.cash = cls.env['account.journal'].search([('type', '=', 'cash'), ('company_id', '=', cls.company.id)], limit=1)
        cls.bank = cls.env['account.journal'].search([('type', '=', 'bank'), ('company_id', '=', cls.company.id)], limit=1)

    def _open(self, cash=5000.0):
        return self.env['adk.shop.session.open'].create({'shop_id': self.shop.id, 'opening_cash': cash}).action_open()

    def _sale(self, session, qty=2, journal=None, tendered=0.0):
        wiz = self.env['adk.shop.pos.sale'].create({
            'shop_id': self.shop.id, 'session_id': session.id, 'journal_id': (journal or self.cash).id,
            'amount_tendered': tendered,
            'line_ids': [Command.create({'product_id': self.product.id, 'qty': qty, 'price_unit': 100.0})]})
        return wiz, wiz._validate()

    def test_cannot_sell_without_open_session(self):
        self.assertFalse(self.shop.pos_open)
        action = self.shop.action_open_pos()
        self.assertEqual(action['res_model'], 'adk.shop.session.open')   # propose d'ouvrir la caisse

    def test_simple_mode_has_no_pos(self):
        simple = self.env['adk.shop'].create({'name': 'Simple', 'code': 'SIMPLE', 'state': 'active', 'sale_mode': 'simple'})
        with self.assertRaises(UserError):
            simple.action_open_pos()
        with self.assertRaises(UserError):
            self.env['adk.shop.session'].create({'shop_id': simple.id})

    def test_open_once_only(self):
        self._open()
        self.assertTrue(self.shop.pos_open)
        with self.assertRaises(UserError):
            self._open()

    def test_opening_creates_cash_count(self):
        self._open(5000.0)
        sess = self.shop.open_session_id
        self.assertEqual(sess.opening_cash, 5000.0)
        self.assertEqual(self.shop.cash_balance, 5000.0)
        self.assertEqual(len(sess.cash_move_ids), 1)

    def test_full_day(self):
        if not (self.has_chart and self.cash and self.bank):
            self.skipTest("Plan comptable / journaux absents")
        self._open(5000.0)
        sess = self.shop.open_session_id
        wiz, order1 = self._sale(sess, qty=2, journal=self.cash, tendered=500.0)
        self.assertEqual(wiz.change_due, 300.0)
        self.assertEqual(order1.state, 'sale')
        self.assertEqual(order1.adk_shop_id, self.shop)
        self.assertEqual(order1.picking_ids.state, 'done')                  # stock sorti
        self.assertEqual(order1.invoice_ids.payment_state, 'paid')          # facturé et payé
        self.assertEqual(order1.invoice_ids.adk_shop_id, self.shop)
        _, order2 = self._sale(sess, qty=1, journal=self.bank)
        sess.invalidate_recordset()
        self.assertEqual(sess.sales_count, 2)
        self.assertEqual(sess.sales_total, 300.0)
        self.assertEqual(sess.cash_sales, 200.0)
        self.assertEqual(sess.other_sales, 100.0)
        self.assertEqual(self.shop.cash_balance, 5200.0)                    # fond + ventes espèces seulement
        self.assertEqual({r[0] for r in sess._report_payment_lines()}, {self.cash.name, self.bank.name})
        # clôture avec 50 de manque
        close = self.env['adk.shop.session.close'].create({'session_id': sess.id, 'counted_cash': 5150.0})
        self.assertEqual(close.expected_cash, 5200.0)
        self.assertEqual(close.difference, -50.0)
        action = close.action_close()
        self.assertEqual(sess.state, 'closed')
        self.assertEqual(sess.closing_difference, -50.0)
        self.assertEqual(action['res_model'], 'adk.shop.session')           # la fiche de la journée s'ouvre
        self.assertIn(sess.action_print_z()['type'], ('ir.actions.report', 'ir.actions.act_window'))
        self.assertTrue(self.env['adk.shop.alert'].search([('shop_id', '=', self.shop.id), ('alert_type', '=', 'cash')]))
        self.assertFalse(self.shop.pos_open)
        with self.assertRaises(UserError):                                  # plus de vente sur journée clôturée
            self._sale(sess)
        # le lendemain : nouvelle journée, écart d'ouverture visible
        self._open(5150.0)
        self.assertEqual(self.shop.open_session_id.opening_difference, 0.0)
        self.assertEqual(len(self.shop.session_ids), 2)

    def test_empty_sale_refused(self):
        self._open()
        sess = self.shop.open_session_id
        wiz = self.env['adk.shop.pos.sale'].create({'shop_id': self.shop.id, 'session_id': sess.id,
                                                    'journal_id': (self.cash or self.bank).id})
        with self.assertRaises(UserError):
            wiz._validate()

    def test_cart_screen_data_and_checkout(self):
        if not (self.has_chart and self.cash):
            self.skipTest("Plan comptable / journaux absents")
        self._open(1000.0)
        sess = self.shop.open_session_id
        data = self.env['adk.shop.session'].pos_data(sess.id)
        self.assertEqual(data['shop']['id'], self.shop.id)
        self.assertIn(self.product.id, [p['id'] for p in data['products']])
        self.assertTrue(data['journals'])
        res = self.env['adk.shop.session'].pos_checkout(
            sess.id, [{'product_id': self.product.id, 'qty': 3}], self.cash.id, False, 500.0, True)
        self.assertEqual(res['total'], 300.0)
        self.assertEqual(res['sales_count'], 1)
        self.assertEqual(res['live_cash'], 1300.0)
        self.assertEqual(res['ticket']['type'], 'ir.actions.report') if res['ticket']['type'] == 'ir.actions.report' else None
        action = self.shop.action_open_pos()
        self.assertEqual(action['tag'], 'adk_shop_cart')

    def test_shop_gets_warehouse_automatically(self):
        shop = self.env['adk.shop'].create({'name': 'Sans entrepôt', 'code': 'NOWH', 'state': 'active'})
        self.assertTrue(shop.warehouse_id)
        self.assertEqual(shop.warehouse_id.company_id, shop.company_id)
        shop2 = self.env['adk.shop'].create({'name': 'Sans entrepôt 2', 'code': 'NOWH2', 'state': 'active'})
        self.assertNotEqual(shop.warehouse_id, shop2.warehouse_id)       # un entrepôt par boutique
        # vente simple : l'entrepôt est préchargé
        self.assertEqual(shop.action_new_sale()['context']['default_warehouse_id'], shop.warehouse_id.id)

    def test_order_now_pay_later(self):
        if not (self.has_chart and self.cash):
            self.skipTest("Plan comptable / journaux absents")
        self._open(1000.0)
        sess = self.shop.open_session_id
        S = self.env['adk.shop.session']
        res = S.pos_order(sess.id, [{'product_id': self.product.id, 'qty': 2}])
        order = self.env['sale.order'].browse(res['order_id'])
        # commandé : pas encore livré, facturé, encaissé, ni compté dans les ventes
        self.assertEqual(order.state, 'sale')
        self.assertEqual(order.adk_pos_state, 'pending')
        self.assertFalse(order.invoice_ids)
        self.assertNotIn('done', order.picking_ids.mapped('state'))
        self.assertEqual(len(res['pending']), 1)
        sess.invalidate_recordset()
        self.assertEqual(sess.sales_count, 0)
        self.assertEqual(self.shop.cash_balance, 1000.0)
        # on ne peut pas clôturer avec une commande en attente
        with self.assertRaises(UserError):
            sess._do_close(1000.0)
        # le client revient avec 1 article de plus : mise à jour puis encaissement de la même commande
        S.pos_order(sess.id, [{'product_id': self.product.id, 'qty': 3}], False, order.id)
        self.assertEqual(order.amount_total, 300.0)
        res = S.pos_checkout(sess.id, [{'product_id': self.product.id, 'qty': 3}], self.cash.id, False, 300.0, False, order.id)
        self.assertEqual(res['order_id'], order.id)
        self.assertEqual(order.adk_pos_state, 'paid')
        self.assertEqual(order.invoice_ids.payment_state, 'paid')
        self.assertEqual(set(order.picking_ids.filtered(lambda p: p.state != 'cancel').mapped('state')), {'done'})
        self.assertEqual(res['sales_count'], 1)
        self.assertEqual(res['live_cash'], 1300.0)
        self.assertFalse(res['pending'])
        # annulation d'une commande en attente
        res = S.pos_order(sess.id, [{'product_id': self.product.id, 'qty': 1}])
        self.assertEqual(len(res['pending']), 1)
        self.assertFalse(S.pos_cancel_order(sess.id, res['order_id']))
        sess._do_close(1300.0)
        self.assertEqual(sess.state, 'closed')
