from odoo import api, fields, models
from odoo.exceptions import ValidationError


def _check_shop_company(records, shop_field='adk_shop_id'):
    for rec in records:
        shop = rec[shop_field]
        if shop and shop.company_id != rec.company_id:
            raise ValidationError(rec.env._("La boutique « %(shop)s » n'appartient pas à la société %(company)s.",
                                            shop=shop.display_name, company=rec.company_id.name))


class SaleOrder(models.Model):
    _name = 'sale.order'
    _inherit = ['sale.order', 'adk.open.mixin']

    adk_shop_id = fields.Many2one(
        'adk.shop', string='Boutique', index=True, copy=True, tracking=True,
        default=lambda self: self.env['adk.shop']._get_default_shop(),
        domain="[('company_id', '=', company_id), ('state', '=', 'active')]")

    @api.onchange('adk_shop_id')
    def _onchange_adk_shop_id(self):
        if self.adk_shop_id.warehouse_id:
            self.warehouse_id = self.adk_shop_id.warehouse_id

    @api.constrains('adk_shop_id', 'company_id')
    def _check_adk_shop_company(self):
        _check_shop_company(self)

    def _prepare_invoice(self):
        vals = super()._prepare_invoice()
        vals['adk_shop_id'] = self.adk_shop_id.id
        return vals


class PurchaseOrder(models.Model):
    _name = 'purchase.order'
    _inherit = ['purchase.order', 'adk.open.mixin']

    adk_shop_id = fields.Many2one(
        'adk.shop', string='Boutique', index=True, copy=True, tracking=True,
        default=lambda self: self.env['adk.shop']._get_default_shop(),
        domain="[('company_id', '=', company_id), ('state', '=', 'active')]")

    @api.onchange('adk_shop_id')
    def _onchange_adk_shop_id(self):
        wh = self.adk_shop_id.warehouse_id
        if wh and wh.in_type_id:
            self.picking_type_id = wh.in_type_id

    @api.constrains('adk_shop_id', 'company_id')
    def _check_adk_shop_company(self):
        _check_shop_company(self)

    def _prepare_invoice(self):
        vals = super()._prepare_invoice()
        vals['adk_shop_id'] = self.adk_shop_id.id
        return vals


class AccountMove(models.Model):
    _name = 'account.move'
    _inherit = ['account.move', 'adk.open.mixin']

    adk_shop_id = fields.Many2one(
        'adk.shop', string='Boutique', index=True, copy=True, tracking=True,
        domain="[('company_id', '=', company_id)]")

    @api.constrains('adk_shop_id', 'company_id')
    def _check_adk_shop_company(self):
        _check_shop_company(self)


class AccountMoveLine(models.Model):
    _inherit = 'account.move.line'

    adk_shop_id = fields.Many2one(related='move_id.adk_shop_id', store=True, index=True, string='Boutique')


class AccountPayment(models.Model):
    _name = 'account.payment'
    _inherit = ['account.payment', 'adk.open.mixin']

    adk_shop_id = fields.Many2one(
        'adk.shop', string='Boutique', index=True, store=True, readonly=False,
        compute='_compute_adk_shop_id', domain="[('company_id', '=', company_id)]",
        help="Reprise automatiquement de la facture réglée ; modifiable pour un paiement sans facture.")

    @api.depends('reconciled_invoice_ids.adk_shop_id', 'reconciled_bill_ids.adk_shop_id')
    def _compute_adk_shop_id(self):
        for payment in self:
            shops = (payment.reconciled_invoice_ids | payment.reconciled_bill_ids).adk_shop_id
            if len(shops) == 1:
                payment.adk_shop_id = shops
            elif not payment.adk_shop_id:
                payment.adk_shop_id = False


class StockPicking(models.Model):
    _name = 'stock.picking'
    _inherit = ['stock.picking', 'adk.open.mixin']

    adk_shop_id = fields.Many2one('adk.shop', string='Boutique', compute='_compute_adk_shop_id',
                                  store=True, index=True)

    @api.depends('picking_type_id.warehouse_id')
    def _compute_adk_shop_id(self):
        Shop = self.env['adk.shop'].sudo().with_context(active_test=False)
        for picking in self:
            wh = picking.picking_type_id.warehouse_id
            picking.adk_shop_id = Shop.search([('warehouse_id', '=', wh.id)], limit=1) if wh else False


class StockMove(models.Model):
    _inherit = 'stock.move'

    adk_shop_id = fields.Many2one(related='picking_id.adk_shop_id', store=True, index=True, string='Boutique')
