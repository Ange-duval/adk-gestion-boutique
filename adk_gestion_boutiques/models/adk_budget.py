from odoo import api, fields, models
from odoo.exceptions import ValidationError


class AdkShopBudget(models.Model):
    _name = 'adk.shop.budget'
    _description = 'Budget de boutique'
    _inherit = ['mail.thread', 'adk.open.mixin']
    _order = 'date_from desc, id desc'

    name = fields.Char(string='Intitulé', required=True)
    shop_id = fields.Many2one('adk.shop', string='Boutique', required=True, index=True, tracking=True)
    company_id = fields.Many2one(related='shop_id.company_id', store=True, index=True)
    currency_id = fields.Many2one(related='shop_id.currency_id', store=True)
    date_from = fields.Date(string='Du', required=True)
    date_to = fields.Date(string='Au', required=True)
    state = fields.Selection(
        [('draft', 'Brouillon'), ('confirmed', 'En cours'), ('closed', 'Clôturé')],
        default='draft', required=True, tracking=True, string='État')
    alert_percent = fields.Float(string="Seuil d'alerte (%)", default=90.0)
    alert_sent = fields.Boolean(copy=False)
    line_ids = fields.One2many('adk.shop.budget.line', 'budget_id', string='Lignes budgétaires', copy=True)
    total_planned = fields.Monetary(compute='_compute_totals', currency_field='currency_id', string='Prévu')
    total_spent = fields.Monetary(compute='_compute_totals', currency_field='currency_id', string='Réalisé')
    percent = fields.Float(compute='_compute_totals', string='Consommation (%)')

    @api.constrains('date_from', 'date_to')
    def _check_dates(self):
        for rec in self:
            if rec.date_to < rec.date_from:
                raise ValidationError(self.env._("La date de fin doit être postérieure à la date de début."))

    @api.depends('line_ids.planned_amount', 'line_ids.spent_amount')
    def _compute_totals(self):
        for rec in self:
            rec.total_planned = sum(rec.line_ids.mapped('planned_amount'))
            rec.total_spent = sum(rec.line_ids.mapped('spent_amount'))
            rec.percent = (rec.total_spent / rec.total_planned * 100.0) if rec.total_planned else 0.0

    def _spent_by_category(self):
        self.ensure_one()
        rows = self.env['adk.shop.expense'].sudo()._read_group(
            [('shop_id', '=', self.shop_id.id), ('date', '>=', self.date_from), ('date', '<=', self.date_to),
             ('state', 'in', ('approved', 'billed'))], ['category_id'], ['amount:sum'])
        return {cat.id: total for cat, total in rows}

    def action_confirm(self):
        self.write({'state': 'confirmed'})

    def action_close(self):
        self.write({'state': 'closed'})

    def action_draft(self):
        self.write({'state': 'draft', 'alert_sent': False})


class AdkShopBudgetLine(models.Model):
    _name = 'adk.shop.budget.line'
    _description = 'Ligne de budget de boutique'

    budget_id = fields.Many2one('adk.shop.budget', required=True, ondelete='cascade', index=True)
    currency_id = fields.Many2one(related='budget_id.currency_id')
    category_id = fields.Many2one('adk.expense.category', string='Catégorie', required=True)
    planned_amount = fields.Monetary(string='Prévu', currency_field='currency_id', required=True)
    spent_amount = fields.Monetary(string='Réalisé', currency_field='currency_id', compute='_compute_spent')
    remaining_amount = fields.Monetary(string='Reste', currency_field='currency_id', compute='_compute_spent')
    percent = fields.Float(string='Consommation (%)', compute='_compute_spent')

    _sql_constraints = [
        ('category_uniq', 'unique(budget_id, category_id)', "Une catégorie ne peut apparaître qu'une fois par budget."),
        ('planned_positive', 'check(planned_amount >= 0)', "Le montant prévu ne peut pas être négatif."),
    ]

    @api.depends('planned_amount', 'budget_id.date_from', 'budget_id.date_to', 'budget_id.shop_id', 'category_id')
    def _compute_spent(self):
        cache = {}
        for line in self:
            budget = line.budget_id
            if not budget.id:
                line.spent_amount = line.remaining_amount = line.percent = 0.0
                continue
            if budget.id not in cache:
                cache[budget.id] = budget._spent_by_category()
            spent = cache[budget.id].get(line.category_id.id, 0.0)
            line.spent_amount = spent
            line.remaining_amount = line.planned_amount - spent
            line.percent = (spent / line.planned_amount * 100.0) if line.planned_amount else 0.0
