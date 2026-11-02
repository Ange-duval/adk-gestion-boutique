from odoo import api, fields, models


class ResPartner(models.Model):
    _name = 'res.partner'
    _inherit = ['res.partner', 'adk.open.mixin']

    adk_shop_ids = fields.Many2many(
        'adk.shop', 'adk_shop_partner_rel', 'partner_id', 'shop_id',
        string='Boutiques ADK',
        help="Boutiques autorisées pour ce client / fournisseur. Une même fiche peut être partagée sans doublon.")

    def _adk_sync_tags(self):
        """Maintient les étiquettes natives « Boutique XXX » en phase avec les boutiques liées."""
        all_tags = self.env['adk.shop'].sudo().with_context(active_test=False).search([]).tag_id
        for partner in self:
            wanted = partner.sudo().adk_shop_ids.tag_id
            partner.category_id = (partner.category_id - all_tags) | wanted

    @api.model_create_multi
    def create(self, vals_list):
        partners = super().create(vals_list)
        partners.filtered('adk_shop_ids')._adk_sync_tags()
        return partners

    def write(self, vals):
        res = super().write(vals)
        if 'adk_shop_ids' in vals:
            self._adk_sync_tags()
        return res
