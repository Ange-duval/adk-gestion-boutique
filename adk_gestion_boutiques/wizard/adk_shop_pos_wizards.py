from odoo import Command, api, fields, models
from odoo.exceptions import UserError, ValidationError


class AdkShopSessionOpen(models.TransientModel):
    _name = 'adk.shop.session.open'
    _description = 'Ouverture de la caisse de boutique'

    shop_id = fields.Many2one('adk.shop', string='Boutique', required=True, readonly=True)
    currency_id = fields.Many2one(related='shop_id.currency_id')
    last_balance = fields.Monetary(string='Espèces en caisse hier soir', currency_field='currency_id', compute='_compute_last')
    opening_cash = fields.Monetary(string='Fond de caisse compté ce matin', currency_field='currency_id', required=True)
    difference = fields.Monetary(string='Écart avec la veille', currency_field='currency_id', compute='_compute_last')
    note = fields.Text(string='Remarque')

    @api.depends('shop_id', 'opening_cash')
    def _compute_last(self):
        for w in self:
            w.last_balance = w.shop_id._cash_balance_now() if w.shop_id else 0.0
            w.difference = (w.opening_cash - w.last_balance) if w.shop_id.cash_move_ids else 0.0

    def action_open(self):
        self.ensure_one()
        if self.opening_cash < 0:
            raise ValidationError(self.env._("Le fond de caisse ne peut pas être négatif."))
        sess = self.env['adk.shop.session'].create({'shop_id': self.shop_id.id, 'opening_cash': self.opening_cash,
                                                    'note': self.note})
        return {'type': 'ir.actions.client', 'tag': 'display_notification', 'params': {
            'title': self.env._("Caisse ouverte"), 'type': 'success', 'sticky': False,
            'message': self.env._("%(s)s : bonne journée de vente !", s=sess.name),
            'next': self.shop_id.action_open_pos()}}


class AdkShopSessionClose(models.TransientModel):
    _name = 'adk.shop.session.close'
    _description = 'Clôture de la journée de caisse'

    session_id = fields.Many2one('adk.shop.session', string='Journée', required=True, readonly=True)
    shop_id = fields.Many2one(related='session_id.shop_id')
    currency_id = fields.Many2one(related='session_id.currency_id')
    sales_count = fields.Integer(related='session_id.sales_count')
    sales_total = fields.Monetary(related='session_id.sales_total', currency_field='currency_id')
    cash_sales = fields.Monetary(related='session_id.cash_sales', currency_field='currency_id')
    other_sales = fields.Monetary(related='session_id.other_sales', currency_field='currency_id')
    expected_cash = fields.Monetary(string='Espèces attendues en caisse', currency_field='currency_id', compute='_compute_expected')
    counted_cash = fields.Monetary(string='Espèces comptées dans le tiroir', currency_field='currency_id', required=True)
    difference = fields.Monetary(string='Écart', currency_field='currency_id', compute='_compute_expected')
    note = fields.Text(string='Remarque de clôture')

    @api.depends('session_id', 'counted_cash')
    def _compute_expected(self):
        for w in self:
            w.expected_cash = w.session_id.shop_id._cash_balance_now() if w.session_id else 0.0
            w.difference = w.counted_cash - w.expected_cash

    def _close(self):
        self.ensure_one()
        if self.counted_cash < 0:
            raise ValidationError(self.env._("Les espèces comptées ne peuvent pas être négatives."))
        self.session_id._do_close(self.counted_cash, self.note)
        return self.session_id

    def action_close(self):
        """Clôture la journée et affiche sa fiche (le ticket Z reste imprimable depuis la fiche)."""
        sess = self._close()
        return {'type': 'ir.actions.act_window', 'name': sess.name, 'res_model': 'adk.shop.session',
                'res_id': sess.id, 'view_mode': 'form', 'views': [(False, 'form')], 'target': 'main'}

    def action_close_print(self):
        """Clôture la journée et imprime directement le ticket Z."""
        return self._close().action_print_z()


class AdkShopPosSale(models.TransientModel):
    _name = 'adk.shop.pos.sale'
    _description = 'Vente POS (comptoir)'

    shop_id = fields.Many2one('adk.shop', string='Boutique', required=True, readonly=True)
    session_id = fields.Many2one('adk.shop.session', string='Journée', required=True, readonly=True)
    company_id = fields.Many2one(related='shop_id.company_id')
    currency_id = fields.Many2one(related='shop_id.currency_id')
    partner_id = fields.Many2one('res.partner', string='Client', help="Laissez vide pour un client de passage.")
    order_id = fields.Many2one('sale.order', string='Commande en attente', readonly=True)
    line_ids = fields.One2many('adk.shop.pos.sale.line', 'wizard_id', string='Articles')
    journal_id = fields.Many2one('account.journal', string='Mode de paiement',
                                 domain="[('type', 'in', ('cash', 'bank')), ('company_id', '=', company_id)]",
                                 default=lambda self: self._default_journal())
    journal_type = fields.Selection(related='journal_id.type')
    amount_total = fields.Monetary(string='Total à payer', currency_field='currency_id', compute='_compute_total')
    amount_tendered = fields.Monetary(string='Montant reçu', currency_field='currency_id')
    change_due = fields.Monetary(string='Monnaie à rendre', currency_field='currency_id', compute='_compute_total')

    @api.model
    def _default_journal(self):
        return self.env['account.journal'].search([('type', '=', 'cash'), ('company_id', '=', self.env.company.id)], limit=1)

    @api.depends('line_ids.subtotal', 'amount_tendered', 'journal_id')
    def _compute_total(self):
        for w in self:
            w.amount_total = sum(w.line_ids.mapped('subtotal'))
            w.change_due = max(w.amount_tendered - w.amount_total, 0.0) if w.journal_id.type == 'cash' else 0.0

    # ------------------------------------------------------------------ validation
    def _walkin_partner(self):
        return self.env.ref('adk_gestion_boutiques.partner_walkin', raise_if_not_found=False) or \
            self.env['res.partner'].create({'name': self.env._('Client de passage')})

    def _make_order(self):
        """Crée (ou met à jour) la commande client confirmée, sans livraison ni paiement."""
        self.ensure_one()
        sess = self.session_id
        if sess.state != 'open':
            raise UserError(self.env._("La caisse est clôturée : ouvrez une nouvelle journée pour vendre."))
        shop = self.shop_id
        shop._check_can_sell_pos()
        lines = self.line_ids.filtered(lambda l: l.product_id and l.qty)
        if not lines:
            raise UserError(self.env._("Ajoutez au moins un article."))
        if any(l.qty <= 0 for l in lines):
            raise UserError(self.env._("Les quantités doivent être positives."))
        shop._ensure_warehouse()
        cmds = [Command.create({'product_id': l.product_id.id, 'product_uom_qty': l.qty,
                                'price_unit': l.price_unit, 'discount': l.discount}) for l in lines]
        partner = (self.partner_id or self._walkin_partner())
        order = self.order_id
        if order:
            if order.adk_session_id != sess or order.adk_pos_state != 'pending':
                raise UserError(self.env._("Cette commande n'est plus en attente."))
            if order.state == 'sale':
                order.with_context(disable_cancel_warning=True).action_cancel()
            if order.state == 'cancel':
                order.action_draft()
            order.order_line.unlink()
            order.write({'partner_id': partner.id, 'order_line': cmds})
        else:
            order = self.env['sale.order'].create({
                'partner_id': partner.id, 'company_id': shop.company_id.id, 'adk_shop_id': shop.id,
                'warehouse_id': shop.warehouse_id.id, 'adk_session_id': sess.id, 'adk_pos_state': 'pending',
                'order_line': cmds})
        order.action_confirm()
        return order

    def _validate(self):
        self.ensure_one()
        if not self.journal_id:
            raise UserError(self.env._("Choisissez un mode de paiement."))
        if self.journal_id.type == 'cash' and self.amount_tendered and self.amount_tendered + 1e-6 < self.amount_total:
            raise UserError(self.env._("Le montant reçu est inférieur au total à payer."))
        order = self._make_order()
        shop, sess = self.shop_id, self.session_id
        order.write({'adk_shop_pay_journal_id': self.journal_id.id})
        self._deliver(order)
        invoice = order._create_invoices()
        invoice.action_post()
        self.env['account.payment.register'].with_context(active_model='account.move', active_ids=invoice.ids).create({
            'journal_id': self.journal_id.id, 'amount': invoice.amount_residual})._create_payments()
        order.adk_pos_state = 'paid'
        if self.journal_id.type == 'cash':
            self.env['adk.shop.cash.move'].sudo().create({
                'shop_id': shop.id, 'kind': 'in', 'amount': order.amount_total, 'session_id': sess.id,
                'reason': self.env._("Vente comptoir %s", order.name), 'reference': order.name})
        return order

    def _deliver(self, order):
        for picking in order.picking_ids.filtered(lambda p: p.state not in ('done', 'cancel')):
            if picking.state == 'confirmed':
                picking.action_assign()
            for move in picking.move_ids:
                move.quantity = move.product_uom_qty
                move.picked = True
            picking.with_context(skip_immediate=True, skip_backorder=True, skip_sms=True,
                                 picking_ids_not_to_backorder=picking.ids).button_validate()

    def _next_action(self, order):
        return {'type': 'ir.actions.client', 'tag': 'display_notification', 'params': {
            'title': self.env._("Vente enregistrée"), 'type': 'success', 'sticky': False,
            'message': self.env._("%(o)s — %(t)s encaissé (%(j)s).", o=order.name, t=order.currency_id.format(order.amount_total),
                                  j=self.journal_id.name),
            'next': self.shop_id.action_open_pos()}}

    def action_validate(self):
        order = self._validate()
        return self._next_action(order)

    def action_validate_ticket(self):
        order = self._validate()
        return self.env.ref('adk_gestion_boutiques.action_report_adk_ticket').report_action(order)

    def action_close_day(self):
        self.ensure_one()
        return self.session_id.action_close_wizard()


class AdkShopPosSaleLine(models.TransientModel):
    _name = 'adk.shop.pos.sale.line'
    _description = 'Ligne de vente POS'

    wizard_id = fields.Many2one('adk.shop.pos.sale', required=True, ondelete='cascade')
    currency_id = fields.Many2one(related='wizard_id.currency_id')
    product_id = fields.Many2one('product.product', string='Article', domain="[('sale_ok', '=', True)]")
    qty = fields.Float(string='Quantité', default=1.0)
    price_unit = fields.Monetary(string='Prix unitaire', currency_field='currency_id')
    discount = fields.Float(string='Remise %')
    subtotal = fields.Monetary(string='Total TTC', currency_field='currency_id', compute='_compute_subtotal')

    @api.onchange('product_id')
    def _onchange_product_id(self):
        if self.product_id:
            self.price_unit = self.product_id.lst_price

    @api.depends('product_id', 'qty', 'price_unit', 'discount', 'wizard_id.company_id')
    def _compute_subtotal(self):
        for l in self:
            company = l.wizard_id.company_id or self.env.company
            taxes = l.product_id.taxes_id.filtered(lambda t: t.company_id == company)
            if l.product_id:
                res = taxes.compute_all(l.price_unit * (1 - (l.discount or 0.0) / 100.0), l.currency_id or company.currency_id,
                                        l.qty, product=l.product_id)
                l.subtotal = res['total_included']
            else:
                l.subtotal = 0.0
