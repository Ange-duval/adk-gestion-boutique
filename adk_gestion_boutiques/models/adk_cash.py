from odoo import api, fields, models
from odoo.exceptions import UserError, ValidationError


class AdkShopCashMove(models.Model):
    _name = 'adk.shop.cash.move'
    _description = 'Mouvement de caisse de boutique'
    _order = 'id desc'

    name = fields.Char(string='Référence', default='Nouveau', copy=False, readonly=True)
    shop_id = fields.Many2one('adk.shop', string='Boutique', required=True, index=True,
                              default=lambda self: self.env['adk.shop']._get_default_shop(),
                              domain="[('state', '=', 'active')]")
    company_id = fields.Many2one(related='shop_id.company_id', store=True, index=True)
    currency_id = fields.Many2one(related='shop_id.currency_id', store=True)
    date = fields.Datetime(string='Date', default=fields.Datetime.now, readonly=True)
    user_id = fields.Many2one('res.users', string='Utilisateur', default=lambda self: self.env.user, readonly=True)
    kind = fields.Selection(
        [('in', 'Entrée de caisse'), ('out', 'Sortie de caisse'), ('count', 'Comptage / clôture')],
        string='Type', required=True, default='in')
    amount = fields.Monetary(string='Montant', currency_field='currency_id', required=True,
                             help="Entrée ou sortie : montant du mouvement. Comptage : total des espèces comptées dans le tiroir.")
    reason = fields.Char(string='Motif', required=True)
    reference = fields.Char(string='Pièce / référence')
    expected = fields.Monetary(string='Solde attendu', currency_field='currency_id', readonly=True)
    difference = fields.Monetary(string='Écart', currency_field='currency_id', readonly=True)
    balance_after = fields.Monetary(string='Solde après', currency_field='currency_id', readonly=True)

    @api.constrains('amount', 'kind')
    def _check_amount(self):
        for rec in self:
            if rec.amount < 0 or (rec.kind != 'count' and rec.amount == 0):
                raise ValidationError(self.env._("Le montant doit être strictement positif (zéro autorisé pour un comptage)."))

    @api.model_create_multi
    def create(self, vals_list):
        records = self.browse()
        for vals in vals_list:  # un par un : chaque mouvement part du solde précédent
            last = self.sudo().search([('shop_id', '=', vals.get('shop_id'))], order='id desc', limit=1)
            before = last.balance_after if last else 0.0
            kind = vals.get('kind', 'in')
            amount = vals.get('amount', 0.0)
            vals = dict(vals)
            if kind == 'in':
                after = before + amount
            elif kind == 'out':
                if amount > before + 1e-6:
                    raise UserError(self.env._(
                        "Sortie impossible : le solde de caisse (%(bal)s) est inférieur au montant demandé. "
                        "Faites d'abord un comptage ou une entrée.", bal=before))
                after = before - amount
            else:
                after = amount
                opening = not last          # premier comptage = fond de caisse : aucun écart à signaler
                vals['expected'] = amount if opening else before
                vals['difference'] = 0.0 if opening else amount - before
            vals['balance_after'] = after
            if vals.get('name', 'Nouveau') == 'Nouveau':
                vals['name'] = self.env['ir.sequence'].next_by_code('adk.shop.cash.move') or 'Nouveau'
            move = super().create([vals])
            records |= move
            if move.kind == 'count' and not move.currency_id.is_zero(move.difference):
                shop = move.shop_id.sudo()
                fmt = lambda v: self.env['adk.shop.alert']._fmt_amount(v, shop)
                self.env['adk.shop.alert'].sudo()._upsert_alert(
                    shop, 'cash', self.env._("Écart de caisse de %s", fmt(move.difference)),
                    self.env._("Comptage de %(count)s pour un solde attendu de %(exp)s (%(ref)s).",
                               count=fmt(move.amount), exp=fmt(move.expected), ref=move.name),
                    '2', 'adk.shop.cash.move', move.id)
        return records

    def write(self, vals):
        if not self.env.su:
            raise UserError(self.env._("Un mouvement de caisse ne se modifie pas : passez un mouvement correctif."))
        return super().write(vals)

    def unlink(self):
        if not self.env.su:
            raise UserError(self.env._("Un mouvement de caisse ne se supprime pas : passez un mouvement correctif."))
        return super().unlink()
