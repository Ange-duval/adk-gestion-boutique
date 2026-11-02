from odoo import fields, models


class ProductTemplate(models.Model):
    _name = 'product.template'
    _inherit = ['product.template', 'adk.open.mixin']

    adk_shop_ids = fields.Many2many(
        'adk.shop', 'adk_shop_product_tmpl_rel', 'product_tmpl_id', 'shop_id',
        string='Boutiques ADK',
        help="Boutiques où ce produit est commercialisé. Laisser vide pour un produit non rattaché.")
