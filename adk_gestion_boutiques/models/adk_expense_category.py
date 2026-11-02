from odoo import fields, models


class AdkExpenseCategory(models.Model):
    _name = 'adk.expense.category'
    _description = 'Catégorie de dépense de boutique'
    _order = 'sequence, name'

    name = fields.Char(string='Catégorie', required=True, translate=True)
    sequence = fields.Integer(default=10)
    active = fields.Boolean(default=True)
    account_id = fields.Many2one(
        'account.account', string='Compte de charge', company_dependent=True,
        domain="[('internal_group', '=', 'expense')]",
        help="Compte utilisé à la création de la facture fournisseur. Par défaut, le premier compte "
             "adapté du plan comptable de la société est utilisé ; vous pouvez le changer ici.")
    note = fields.Text(string='Notes')

    def _default_account(self, company):
        """Compte natif par défaut du plan comptable de la société (modifiable ensuite par l'utilisateur)."""
        Account = self.env['account.account'].with_company(company)
        base = [('company_ids', 'in', company.id), ('internal_group', '=', 'expense')]
        pref = 'expense'
        return Account.search(base + [('account_type', '=', pref)], limit=1, order='code') or Account.search(base, limit=1, order='code')

    _sql_constraints = [('name_uniq', 'unique(name)', "Cette catégorie existe déjà.")]
