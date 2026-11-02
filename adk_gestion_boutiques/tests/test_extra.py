from odoo import Command, fields
from odoo.tests import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestAdkExtra(TransactionCase):
    """Transferts entre sociétés, consolidation en devise, ventes hors connexion."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company_a = cls.env.company
        cls.eur = cls.env.ref('base.EUR')
        cls.env.ref('base.EUR').active = True
        cls.company_b = cls.env['res.company'].create({'name': 'Société B', 'currency_id': cls.eur.id})
        cls.wh_a = cls.env['stock.warehouse'].search([('company_id', '=', cls.company_a.id)], limit=1)
        cls.wh_b = cls.env['stock.warehouse'].search([('company_id', '=', cls.company_b.id)], limit=1)
        cls.shop_a = cls.env['adk.shop'].create({'name': 'Boutique A', 'code': 'XA', 'state': 'active',
                                                 'company_id': cls.company_a.id, 'warehouse_id': cls.wh_a.id})
        cls.shop_b = cls.env['adk.shop'].create({'name': 'Boutique B', 'code': 'XB', 'state': 'active',
                                                 'company_id': cls.company_b.id, 'warehouse_id': cls.wh_b.id})
        cls.product = cls.env['product.product'].create({'name': 'Article inter', 'is_storable': True, 'company_id': False})
        cls.env['stock.quant'].with_company(cls.company_a)._update_available_quantity(cls.product, cls.wh_a.lot_stock_id, 10.0)

    def test_transfer_between_companies(self):
        tr = self.env['adk.shop.transfer'].create({
            'shop_from_id': self.shop_a.id, 'shop_to_id': self.shop_b.id,
            'line_ids': [Command.create({'product_id': self.product.id, 'qty_requested': 4})]})
        tr.action_request()
        tr.action_approve()
        tr.action_ship()
        out = tr.out_picking_id
        self.assertEqual(out.company_id, self.company_a)
        self.assertFalse(out.location_dest_id.company_id)                      # transit commun
        out.move_ids.quantity = 4
        out.with_company(self.company_a).button_validate()
        self.assertEqual(out.state, 'done')
        tr.action_receive()
        inn = tr.in_picking_id
        self.assertEqual(inn.company_id, self.company_b)
        inn.move_ids.quantity = 4
        inn.with_company(self.company_b).button_validate()
        self.assertEqual(inn.state, 'done')
        qty_b = self.product.with_company(self.company_b).with_context(warehouse_id=self.wh_b.id).qty_available
        self.assertEqual(qty_b, 4.0)

    def test_consolidated_report(self):
        wiz = self.env['adk.shop.report.wizard'].create({
            'report_type': 'summary', 'shop_ids': [Command.set((self.shop_a | self.shop_b).ids)],
            'target_currency_id': self.company_a.currency_id.id})
        data = wiz.get_report_data()
        self.assertEqual(len(data['rows']), 2)
        self.assertTrue(data['totals'])                                       # totaux malgré 2 devises
        self.assertEqual({r[2] for r in data['rows']}, {self.company_a.currency_id.name})
        for rtype in ('expenses', 'stock'):
            wiz.report_type = rtype
            wiz.get_report_data()
        wiz.report_type = 'summary'
        wiz.target_currency_id = False
        data = wiz.get_report_data()
        self.assertFalse(data['totals'])                                      # sans conversion : jamais mélangées

    def test_offline_sync_is_idempotent(self):
        cash = self.env['account.journal'].search([('type', '=', 'cash'), ('company_id', '=', self.company_a.id)], limit=1)
        if not cash:
            self.skipTest("Pas de journal de caisse")
        self.shop_a.sale_mode = 'both'
        self.env['adk.shop.session.open'].create({'shop_id': self.shop_a.id, 'opening_cash': 0}).action_open()
        sess = self.shop_a.open_session_id
        S = self.env['adk.shop.session']
        lines = [{'product_id': self.product.id, 'qty': 1}]
        r1 = S.pos_checkout(sess.id, lines, cash.id, False, 0.0, False, False, 'uuid-123')
        r2 = S.pos_checkout(sess.id, lines, cash.id, False, 0.0, False, False, 'uuid-123')   # rejeu après coupure réseau
        self.assertEqual(r1['order_id'], r2['order_id'])
        self.assertEqual(sess.sales_count if sess.invalidate_recordset() is None else 0, 1)
