from odoo import api, fields, models
from odoo.exceptions import UserError


class AdkShopAlert(models.Model):
    _name = 'adk.shop.alert'
    _description = 'Alerte de boutique'
    _order = 'severity desc, date desc, id desc'

    shop_id = fields.Many2one('adk.shop', string='Boutique', required=True, index=True, ondelete='cascade')
    company_id = fields.Many2one(related='shop_id.company_id', store=True, index=True)
    alert_type = fields.Selection(
        [('low_stock', 'Stock sous le minimum'), ('overdue', 'Factures clients en retard'),
         ('budget', 'Budget presque consommé'), ('cash', 'Écart de caisse')], string="Type d'alerte", required=True, index=True)
    severity = fields.Selection([('0', 'Information'), ('1', 'Attention'), ('2', 'Critique')],
                                default='1', required=True, string='Gravité')
    name = fields.Char(string='Alerte', required=True)
    message = fields.Text(string='Détail')
    date = fields.Datetime(string='Date', default=fields.Datetime.now, readonly=True)
    state = fields.Selection([('new', 'Nouvelle'), ('ack', 'Prise en compte'), ('resolved', 'Résolue')],
                             default='new', required=True, string='État', index=True)
    res_model = fields.Char(string='Modèle source')
    res_id = fields.Many2oneReference(model_field='res_model', string='Document source')

    def action_ack(self):
        self.filtered(lambda a: a.state == 'new').write({'state': 'ack'})

    def action_resolve(self):
        self.write({'state': 'resolved'})

    def action_open_source(self):
        self.ensure_one()
        if not self.res_model or not self.res_id:
            raise UserError(self.env._("Cette alerte n'a pas de document source."))
        return {'type': 'ir.actions.act_window', 'res_model': self.res_model, 'res_id': self.res_id, 'view_mode': 'form'}

    @api.model
    def _fmt_amount(self, value, shop):
        """Montant lisible : 1 234 567 FCFA (sans décimales inutiles)."""
        digits = 0 if shop.currency_id.is_zero(value - round(value)) else 2
        text = ('{:,.%df}' % digits).format(value).replace(',', '\u202f')
        return '%s %s' % (text, shop.currency_id.symbol or shop.currency_id.name)

    # ------------------------------------------------------------------------
    # Génération (cron quotidien)
    # ------------------------------------------------------------------------
    @api.model
    def _upsert_alert(self, shop, alert_type, name, message, severity, res_model, res_id):
        alert = self.search([
            ('shop_id', '=', shop.id), ('alert_type', '=', alert_type),
            ('res_model', '=', res_model), ('res_id', '=', res_id), ('state', 'in', ('new', 'ack'))], limit=1)
        if alert:
            alert.write({'name': name, 'message': message, 'severity': severity})
            return alert
        alert = self.create({
            'shop_id': shop.id, 'alert_type': alert_type, 'name': name, 'message': message,
            'severity': severity, 'res_model': res_model, 'res_id': res_id})
        if shop.manager_id:
            shop.activity_schedule('mail.mail_activity_data_todo', user_id=shop.manager_id.id,
                                   summary=name, note=message)
        return alert

    @api.model
    def _close_obsolete(self, alert_type, active_keys):
        stale = self.search([('alert_type', '=', alert_type), ('state', 'in', ('new', 'ack'))]).filtered(
            lambda a: (a.shop_id.id, a.res_model, a.res_id) not in active_keys)
        stale.write({'state': 'resolved'})

    @api.model
    def _cron_generate_alerts(self):
        self = self.sudo()
        shops = self.env['adk.shop'].search([('state', '=', 'active')])
        self._gen_low_stock(shops)
        self._gen_overdue(shops)
        self._gen_budgets()

    @api.model
    def _gen_low_stock(self, shops):
        keys = set()
        for shop in shops.filtered('warehouse_id'):
            orderpoints = self.env['stock.warehouse.orderpoint'].search([('warehouse_id', '=', shop.warehouse_id.id)])
            for op in orderpoints:
                if op.product_min_qty and op.qty_on_hand < op.product_min_qty:
                    keys.add((shop.id, 'stock.warehouse.orderpoint', op.id))
                    self._upsert_alert(
                        shop, 'low_stock', self.env._("Stock bas : %s", op.product_id.display_name),
                        self.env._("Stock %(qty)s pour un minimum de %(min)s.", qty=op.qty_on_hand, min=op.product_min_qty),
                        '1', 'stock.warehouse.orderpoint', op.id)
        self._close_obsolete('low_stock', keys)

    @api.model
    def _gen_overdue(self, shops):
        today = fields.Date.context_today(self)
        keys = set()
        rows = self.env['account.move']._read_group(
            [('adk_shop_id', 'in', shops.ids), ('state', '=', 'posted'), ('move_type', '=', 'out_invoice'),
             ('payment_state', 'in', ('not_paid', 'partial')), ('invoice_date_due', '<', today)],
            ['adk_shop_id'], ['__count', 'amount_residual_signed:sum'])
        for shop, count, total in rows:
            keys.add((shop.id, 'adk.shop', shop.id))
            self._upsert_alert(
                shop, 'overdue', self.env._("%s facture(s) client en retard", count),
                self.env._("Montant restant dû : %s.", self._fmt_amount(total, shop)),
                '2' if count >= 5 else '1', 'adk.shop', shop.id)
        self._close_obsolete('overdue', keys)

    @api.model
    def _gen_budgets(self):
        keys = set()
        for budget in self.env['adk.shop.budget'].search([('state', '=', 'confirmed')]):
            if budget.total_planned and budget.percent >= budget.alert_percent:
                keys.add((budget.shop_id.id, 'adk.shop.budget', budget.id))
                self._upsert_alert(
                    budget.shop_id, 'budget',
                    self.env._("Budget « %(name)s » consommé à %(pct)s %%", name=budget.name, pct=round(budget.percent)),
                    self.env._("Réalisé %(spent)s sur %(planned)s prévus.",
                               spent=self._fmt_amount(budget.total_spent, budget.shop_id),
                               planned=self._fmt_amount(budget.total_planned, budget.shop_id)),
                    '2' if budget.percent >= 100 else '1', 'adk.shop.budget', budget.id)
        self._close_obsolete('budget', keys)
