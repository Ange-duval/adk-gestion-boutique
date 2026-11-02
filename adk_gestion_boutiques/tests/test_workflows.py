from odoo import Command, fields
from odoo.exceptions import UserError
from odoo.tests import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestAdkWorkflows(TransactionCase):
    """Parcours complets sur de vrais documents natifs (nécessite un plan comptable installé)."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env.company
        cls.has_chart = bool(cls.company.chart_template) or bool(
            cls.env['account.journal'].search([('type', '=', 'sale'), ('company_id', '=', cls.company.id)], limit=1))
        cls.warehouse = cls.env['stock.warehouse'].search([('company_id', '=', cls.company.id)], limit=1)
        cls.shop = cls.env['adk.shop'].create({
            'name': 'Boutique Flux', 'code': 'FLUX', 'state': 'active', 'warehouse_id': cls.warehouse.id})
        cls.partner = cls.env['res.partner'].create({'name': 'Client Flux', 'customer_rank': 1})
        cls.product = cls.env['product.product'].create({
            'name': 'Article Flux', 'is_storable': True, 'list_price': 100.0, 'standard_price': 40.0,
            'taxes_id': [Command.clear()], 'supplier_taxes_id': [Command.clear()]})
        cls.env['stock.quant']._update_available_quantity(cls.product, cls.warehouse.lot_stock_id, 10.0)

    def _sale(self, qty=2):
        return self.env['sale.order'].create({
            'partner_id': self.partner.id, 'adk_shop_id': self.shop.id, 'warehouse_id': self.warehouse.id,
            'order_line': [Command.create({'product_id': self.product.id, 'product_uom_qty': qty})]})

    def test_sale_to_payment(self):
        if not self.has_chart:
            self.skipTest("Plan comptable absent")
        order = self._sale()
        order.action_confirm()
        picking = order.picking_ids
        self.assertEqual(picking.adk_shop_id, self.shop)                    # livraison rattachée à la boutique
        self.assertEqual(self.shop.stock_move_ids, picking.move_ids)
        invoice = order._create_invoices()
        self.assertEqual(invoice.adk_shop_id, self.shop)                    # facture rattachée
        invoice.action_post()
        self.shop.invalidate_recordset()
        self.assertEqual(self.shop.kpi_revenue, 200.0)
        self.assertEqual(self.shop.kpi_cogs, 80.0)
        self.assertEqual(self.shop.kpi_margin, 120.0)
        self.assertEqual(self.shop.kpi_receivable, 200.0)
        self.assertEqual(self.shop.kpi_sales_count, 1)
        self.assertIn(invoice, self.shop.invoice_ids)
        self.assertIn(order, self.shop.sale_order_ids)
        payment = self.env['account.payment.register'].with_context(
            active_model='account.move', active_ids=invoice.ids).create({})._create_payments()
        self.assertEqual(payment.adk_shop_id, self.shop)                    # encaissement rattaché
        self.shop.invalidate_recordset()
        self.assertEqual(self.shop.kpi_collected, 200.0)
        self.assertEqual(self.shop.kpi_receivable, 0.0)
        self.assertIn(payment, self.shop.payment_in_ids)

    def test_purchase_and_reports(self):
        if not self.has_chart:
            self.skipTest("Plan comptable absent")
        supplier = self.env['res.partner'].create({'name': 'Fournisseur Flux', 'supplier_rank': 1})
        po = self.env['purchase.order'].create({
            'partner_id': supplier.id, 'adk_shop_id': self.shop.id,
            'picking_type_id': self.warehouse.in_type_id.id,
            'order_line': [Command.create({'product_id': self.product.id, 'product_qty': 5, 'price_unit': 30.0})]})
        po.button_confirm()
        self.assertEqual(po.picking_ids.adk_shop_id, self.shop)
        po.picking_ids.move_ids.quantity = 5
        po.picking_ids.button_validate()
        po.action_create_invoice()
        bill = po.invoice_ids
        self.assertEqual(bill.adk_shop_id, self.shop)
        bill.invoice_date = fields.Date.today()
        bill.action_post()
        self.shop.invalidate_recordset()
        self.assertEqual(self.shop.kpi_goods_purchases, 150.0)   # marchandises stockées : pas une charge
        self.assertEqual(self.shop.kpi_charges, 0.0)
        self.assertEqual(self.shop.kpi_payable, 150.0)
        wiz = self.env['adk.shop.report.wizard'].create({'report_type': 'summary', 'shop_ids': [(6, 0, self.shop.ids)]})
        data = wiz.get_report_data()
        row = data['rows'][0]
        self.assertEqual(row[8], 150.0)
        wiz.action_print_pdf()
        action = wiz.action_export_xlsx()
        self.assertIn('/web/content/', action['url'])

    def test_stock_value_and_transfer(self):
        other_wh = self.env['stock.warehouse'].create({'name': 'Entrepôt B', 'code': 'EWB'})
        other = self.env['adk.shop'].create({'name': 'Boutique B', 'code': 'FLUXB', 'state': 'active',
                                             'warehouse_id': other_wh.id})
        self.shop.invalidate_recordset()
        self.assertEqual(self.shop.kpi_stock_value, 400.0)
        self.assertTrue(self.shop.quant_ids)
        transfer = self.env['adk.shop.transfer'].create({
            'shop_from_id': self.shop.id, 'shop_to_id': other.id,
            'line_ids': [Command.create({'product_id': self.product.id, 'qty_requested': 3})]})
        transfer.action_request()
        transfer.action_approve()
        transfer.action_ship()
        out = transfer.out_picking_id
        self.assertEqual(out.adk_shop_id, self.shop)
        with self.assertRaises(UserError):
            transfer.action_receive()          # la sortie n'est pas encore validée
        out.move_ids.quantity = 3
        out.button_validate()
        transfer.action_receive()
        self.assertEqual(transfer.in_picking_id.adk_shop_id, other)
        transfer.in_picking_id.move_ids.quantity = 3
        transfer.in_picking_id.button_validate()
        other.invalidate_recordset()
        self.shop.invalidate_recordset()
        self.assertEqual(other.kpi_stock_value, 120.0)
        self.assertEqual(self.shop.kpi_stock_value, 280.0)

    def test_restricted_group_sees_only_own_shops(self):
        other = self.env['adk.shop'].create({'name': 'Boutique C', 'code': 'FLUXC', 'state': 'active'})
        user = self.env['res.users'].create({
            'name': 'Cloisonné', 'login': 'cloisonne_adk',
            'groups_id': [Command.set([
                self.env.ref('adk_gestion_boutiques.group_adk_user').id,
                self.env.ref('adk_gestion_boutiques.group_adk_restricted').id,
                self.env.ref('sales_team.group_sale_salesman').id])]})
        self.shop.user_ids = [Command.link(user.id)]
        own = self._sale()
        foreign = self.env['sale.order'].create({'partner_id': self.partner.id, 'adk_shop_id': other.id})
        free = self.env['sale.order'].create({'partner_id': self.partner.id})
        visible = self.env['sale.order'].with_user(user).search([('id', 'in', (own | foreign | free).ids)])
        self.assertEqual(visible, own | free)
        self.assertEqual(self.env['adk.shop'].with_user(user).search([('id', 'in', (self.shop | other).ids)]), self.shop)
