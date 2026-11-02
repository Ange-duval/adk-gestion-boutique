from odoo import Command, api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError


class AdkShopExpense(models.Model):
    _name = 'adk.shop.expense'
    _description = 'Dépense de boutique'
    _inherit = ['mail.thread', 'mail.activity.mixin', 'adk.open.mixin']
    _order = 'date desc, id desc'

    name = fields.Char(string='Référence', default='Nouveau', copy=False, readonly=True)
    shop_id = fields.Many2one(
        'adk.shop', string='Boutique', required=True, index=True, tracking=True,
        default=lambda self: self.env['adk.shop']._get_default_shop(),
        domain="[('state', '=', 'active')]")
    company_id = fields.Many2one(related='shop_id.company_id', store=True, index=True)
    currency_id = fields.Many2one(related='shop_id.currency_id', store=True)
    category_id = fields.Many2one('adk.expense.category', string='Catégorie', required=True, tracking=True)
    date = fields.Date(string='Date', required=True, default=fields.Date.context_today, tracking=True)
    description = fields.Char(string='Libellé', required=True)
    amount = fields.Monetary(string='Montant', currency_field='currency_id', required=True, tracking=True)
    partner_id = fields.Many2one('res.partner', string='Fournisseur / bénéficiaire')
    user_id = fields.Many2one('res.users', string='Demandeur', default=lambda self: self.env.user, readonly=True)
    approver_id = fields.Many2one('res.users', string='Approuvé par', readonly=True, copy=False)
    attachment_ids = fields.Many2many(
        'ir.attachment', 'adk_expense_attachment_rel', 'expense_id', 'attachment_id', string='Justificatifs')
    state = fields.Selection(
        [('draft', 'Brouillon'), ('submitted', 'À approuver'), ('approved', 'Approuvée'),
         ('billed', 'Facturée'), ('rejected', 'Refusée')],
        string='État', default='draft', required=True, tracking=True, copy=False)
    reject_reason = fields.Text(string='Motif du refus', copy=False)
    move_id = fields.Many2one('account.move', string='Facture fournisseur', readonly=True, copy=False)
    payment_state = fields.Selection(related='move_id.payment_state', string='Paiement')

    @api.constrains('amount')
    def _check_amount(self):
        for rec in self:
            if rec.amount <= 0:
                raise ValidationError(self.env._("Le montant d'une dépense doit être strictement positif."))


    _ADK_GUARDED = {'approver_id', 'state', 'move_id'}

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
                vals['name'] = self.env['ir.sequence'].next_by_code('adk.shop.expense') or 'Nouveau'
        return super().create(vals_list)

    def unlink(self):
        if any(rec.state not in ('draft', 'rejected') for rec in self):
            raise UserError(self.env._("Seules les dépenses en brouillon ou refusées peuvent être supprimées."))
        return super().unlink()

    # ------------------------------------------------------------------------
    def _can_approve(self):
        self.ensure_one()
        user = self.env.user
        if user.has_group('adk_gestion_boutiques.group_adk_director'):
            return True
        if not user.has_group('adk_gestion_boutiques.group_adk_manager'):
            return False
        if self.user_id == user:
            return False  # pas d'auto-approbation pour un simple responsable
        shop = self.shop_id
        return user in (shop.manager_id | shop.group_id.manager_id)

    def action_submit(self):
        self = self.with_context(adk_internal=True)
        for rec in self.filtered(lambda r: r.state == 'draft'):
            limit = rec.shop_id.expense_auto_approve_limit
            if limit and rec.amount <= limit:
                rec.write({'state': 'approved', 'approver_id': self.env.user.id})
                rec.message_post(body=self.env._("Approbation automatique (montant inférieur ou égal au seuil de la boutique)."))
            else:
                rec.state = 'submitted'
                manager = rec.shop_id.manager_id or rec.shop_id.group_id.manager_id
                if manager:
                    rec.activity_schedule(
                        'mail.mail_activity_data_todo', user_id=manager.id,
                        summary=self.env._("Dépense à approuver : %s", rec.name))

    def action_approve(self):
        self = self.with_context(adk_internal=True)
        for rec in self.filtered(lambda r: r.state == 'submitted'):
            if not rec._can_approve():
                raise UserError(self.env._("Vous n'avez pas le droit d'approuver cette dépense."))
            rec.write({'state': 'approved', 'approver_id': self.env.user.id})
            rec.activity_feedback(['mail.mail_activity_data_todo'])

    def action_reject(self):
        self = self.with_context(adk_internal=True)
        for rec in self.filtered(lambda r: r.state == 'submitted'):
            if not rec._can_approve():
                raise UserError(self.env._("Vous n'avez pas le droit de refuser cette dépense."))
            if not rec.reject_reason:
                raise UserError(self.env._("Renseignez le motif du refus avant de refuser."))
            rec.state = 'rejected'
            rec.activity_feedback(['mail.mail_activity_data_todo'])

    def action_reset_draft(self):
        self = self.with_context(adk_internal=True)
        self.filtered(lambda r: r.state == 'rejected').write({'state': 'draft', 'reject_reason': False})

    def action_create_bill(self):
        self = self.with_context(adk_internal=True)
        for rec in self:
            if rec.state != 'approved' or rec.move_id:
                raise UserError(self.env._("Seule une dépense approuvée et non encore facturée peut générer une facture."))
            if not rec.partner_id:
                raise UserError(self.env._("Renseignez le fournisseur / bénéficiaire."))
            account = rec.category_id.with_company(rec.company_id).account_id or rec.category_id._default_account(rec.company_id)
            if not account:
                raise UserError(self.env._(
                    "Aucun compte de charge n'est défini pour la catégorie « %(cat)s » dans la société %(company)s.",
                    cat=rec.category_id.name, company=rec.company_id.name))
            move = self.env['account.move'].with_company(rec.company_id).create({
                'move_type': 'in_invoice',
                'partner_id': rec.partner_id.id,
                'invoice_date': rec.date,
                'ref': rec.name,
                'adk_shop_id': rec.shop_id.id,
                'invoice_line_ids': [Command.create({
                    'name': rec.description,
                    'quantity': 1.0,
                    'price_unit': rec.amount,
                    'account_id': account.id,
                    'tax_ids': [Command.clear()],
                })],
            })
            rec.write({'move_id': move.id, 'state': 'billed'})
            rec.message_post(body=self.env._("Facture fournisseur %s créée (brouillon).", move.display_name))

    def action_open_bill(self):
        self.ensure_one()
        return {'type': 'ir.actions.act_window', 'res_model': 'account.move', 'res_id': self.move_id.id,
                'view_mode': 'form'}
