from odoo import Command, api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError


class AdkIncomeCategory(models.Model):
    _name = 'adk.income.category'
    _description = "Catégorie d'autre revenu de boutique"
    _order = 'sequence, name'

    name = fields.Char(string='Catégorie', required=True, translate=True)
    sequence = fields.Integer(default=10)
    active = fields.Boolean(default=True)
    account_id = fields.Many2one(
        'account.account', string='Compte de produit', company_dependent=True,
        domain="[('internal_group', '=', 'income')]",
        help="Compte utilisé à la création de la facture client. Par défaut, le premier compte "
             "adapté du plan comptable de la société est utilisé ; vous pouvez le changer ici.")
    note = fields.Text(string='Notes')

    def _default_account(self, company):
        """Compte natif par défaut du plan comptable de la société (modifiable ensuite par l'utilisateur)."""
        Account = self.env['account.account'].with_company(company)
        base = [('company_ids', 'in', company.id), ('internal_group', '=', 'income')]
        pref = 'income'
        return Account.search(base + [('account_type', '=', pref)], limit=1, order='code') or Account.search(base, limit=1, order='code')

    _sql_constraints = [('name_uniq', 'unique(name)', "Cette catégorie existe déjà.")]


class AdkShopIncome(models.Model):
    _name = 'adk.shop.income'
    _description = 'Autre revenu de boutique'
    _inherit = ['mail.thread', 'mail.activity.mixin', 'adk.open.mixin']
    _order = 'date desc, id desc'

    _ADK_GUARDED = {'state', 'confirmer_id', 'move_id'}

    name = fields.Char(string='Référence', default='Nouveau', copy=False, readonly=True)
    shop_id = fields.Many2one(
        'adk.shop', string='Boutique', required=True, index=True, tracking=True,
        default=lambda self: self.env['adk.shop']._get_default_shop(),
        domain="[('state', '=', 'active')]")
    company_id = fields.Many2one(related='shop_id.company_id', store=True, index=True)
    currency_id = fields.Many2one(related='shop_id.currency_id', store=True)
    category_id = fields.Many2one('adk.income.category', string='Catégorie', required=True, tracking=True)
    date = fields.Date(string='Date', required=True, default=fields.Date.context_today, tracking=True)
    description = fields.Char(string='Libellé', required=True)
    amount = fields.Monetary(string='Montant', currency_field='currency_id', required=True, tracking=True)
    partner_id = fields.Many2one('res.partner', string='Client / payeur')
    user_id = fields.Many2one('res.users', string='Saisi par', default=lambda self: self.env.user, readonly=True)
    confirmer_id = fields.Many2one('res.users', string='Confirmé par', readonly=True, copy=False)
    attachment_ids = fields.Many2many(
        'ir.attachment', 'adk_income_attachment_rel', 'income_id', 'attachment_id', string='Justificatifs')
    state = fields.Selection(
        [('draft', 'Brouillon'), ('confirmed', 'Confirmé'), ('invoiced', 'Facturé')],
        string='État', default='draft', required=True, tracking=True, copy=False)
    move_id = fields.Many2one('account.move', string='Facture client', readonly=True, copy=False)
    payment_state = fields.Selection(related='move_id.payment_state', string='Paiement')

    @api.constrains('amount')
    def _check_amount(self):
        for rec in self:
            if rec.amount <= 0:
                raise ValidationError(self.env._("Le montant d'un revenu doit être strictement positif."))

    def _adk_check_guarded(self, vals):
        if self.env.context.get('adk_internal') or self.env.su:
            return
        if self._ADK_GUARDED & set(vals) and not self.env.user.has_group('adk_gestion_boutiques.group_adk_director'):
            raise AccessError(self.env._("Ces champs ne peuvent être modifiés que par les boutons du workflow."))

    def write(self, vals):
        self._adk_check_guarded(vals)
        return super().write(vals)

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if not (self.env.context.get('adk_internal') or self.env.su):
                for key in self._ADK_GUARDED:
                    vals.pop(key, None)
            if vals.get('name', 'Nouveau') == 'Nouveau':
                vals['name'] = self.env['ir.sequence'].next_by_code('adk.shop.income') or 'Nouveau'
        return super().create(vals_list)

    def unlink(self):
        if any(rec.state != 'draft' for rec in self):
            raise UserError(self.env._("Seuls les revenus en brouillon peuvent être supprimés."))
        return super().unlink()

    def _can_confirm(self):
        self.ensure_one()
        user = self.env.user
        if user.has_group('adk_gestion_boutiques.group_adk_director'):
            return True
        if not user.has_group('adk_gestion_boutiques.group_adk_manager') or self.user_id == user:
            return False
        shop = self.shop_id
        return user in (shop.manager_id | shop.group_id.manager_id)

    def action_confirm(self):
        self = self.with_context(adk_internal=True)
        for rec in self.filtered(lambda r: r.state == 'draft'):
            if not rec._can_confirm():
                raise UserError(self.env._("Vous n'avez pas le droit de confirmer ce revenu (responsable de la boutique requis)."))
            rec.write({'state': 'confirmed', 'confirmer_id': self.env.user.id})

    def action_reset_draft(self):
        self = self.with_context(adk_internal=True)
        for rec in self.filtered(lambda r: r.state == 'confirmed'):
            if not rec._can_confirm():
                raise UserError(self.env._("Vous n'avez pas le droit de remettre ce revenu en brouillon."))
            rec.write({'state': 'draft', 'confirmer_id': False})

    def action_create_invoice(self):
        self = self.with_context(adk_internal=True)
        for rec in self:
            if rec.state != 'confirmed' or rec.move_id:
                raise UserError(self.env._("Seul un revenu confirmé et non encore facturé peut générer une facture."))
            if not rec.partner_id:
                raise UserError(self.env._("Renseignez le client / payeur."))
            account = rec.category_id.with_company(rec.company_id).account_id or rec.category_id._default_account(rec.company_id)
            if not account:
                raise UserError(self.env._(
                    "Aucun compte de produit n'est défini pour la catégorie « %(cat)s » dans la société %(company)s.",
                    cat=rec.category_id.name, company=rec.company_id.name))
            move = self.env['account.move'].with_company(rec.company_id).create({
                'move_type': 'out_invoice',
                'partner_id': rec.partner_id.id,
                'invoice_date': rec.date,
                'ref': rec.name,
                'adk_shop_id': rec.shop_id.id,
                'invoice_line_ids': [Command.create({
                    'name': rec.description, 'quantity': 1.0, 'price_unit': rec.amount,
                    'account_id': account.id, 'tax_ids': [Command.clear()],
                })],
            })
            rec.write({'move_id': move.id, 'state': 'invoiced'})
            rec.message_post(body=self.env._("Facture client %s créée (brouillon).", move.display_name))

    def action_open_invoice(self):
        self.ensure_one()
        return {'type': 'ir.actions.act_window', 'res_model': 'account.move', 'res_id': self.move_id.id,
                'view_mode': 'form', 'views': [(False, 'form')]}
