from odoo import Command, api, fields, models
from odoo.exceptions import UserError, ValidationError


class AdkShopSession(models.Model):
    """Journée de caisse : le boutiquier ouvre sa boutique le matin, vend, puis clôture le soir."""
    _name = 'adk.shop.session'
    _description = 'Journée de caisse de boutique'
    _inherit = ['mail.thread']
    _order = 'id desc'

    name = fields.Char(string='Référence', default='Nouveau', copy=False, readonly=True)
    shop_id = fields.Many2one('adk.shop', string='Boutique', required=True, index=True, readonly=True,
                              domain="[('state', '=', 'active')]")
    company_id = fields.Many2one(related='shop_id.company_id', store=True, index=True)
    currency_id = fields.Many2one(related='shop_id.currency_id', store=True)
    user_id = fields.Many2one('res.users', string='Ouverte par', default=lambda self: self.env.user, readonly=True)
    close_user_id = fields.Many2one('res.users', string='Clôturée par', readonly=True)
    state = fields.Selection([('open', 'Caisse ouverte'), ('closed', 'Journée clôturée')], string='État',
                             default='open', required=True, tracking=True, index=True)
    date_open = fields.Datetime(string='Ouverture', default=fields.Datetime.now, readonly=True)
    date_close = fields.Datetime(string='Clôture', readonly=True)
    opening_cash = fields.Monetary(string='Fond de caisse compté', currency_field='currency_id', readonly=True)
    opening_expected = fields.Monetary(string='Solde attendu à l\'ouverture', currency_field='currency_id', readonly=True)
    opening_difference = fields.Monetary(string='Écart à l\'ouverture', currency_field='currency_id', readonly=True)
    closing_cash = fields.Monetary(string='Espèces comptées à la clôture', currency_field='currency_id', readonly=True)
    closing_expected = fields.Monetary(string='Espèces attendues', currency_field='currency_id', readonly=True)
    closing_difference = fields.Monetary(string='Écart de caisse', currency_field='currency_id', readonly=True)
    note = fields.Text(string='Remarques')
    order_ids = fields.One2many('sale.order', 'adk_session_id', string='Ventes')
    cash_move_ids = fields.One2many('adk.shop.cash.move', 'session_id', string='Mouvements de caisse')
    pending_count = fields.Integer(string='Commandes en attente', compute='_compute_pending')
    sales_count = fields.Integer(string='Nombre de ventes', compute='_compute_totals')
    sales_untaxed = fields.Monetary(string='Total HT', currency_field='currency_id', compute='_compute_totals')
    sales_total = fields.Monetary(string='Total des ventes TTC', currency_field='currency_id', compute='_compute_totals')
    cash_sales = fields.Monetary(string='Ventes en espèces', currency_field='currency_id', compute='_compute_totals')
    other_sales = fields.Monetary(string='Ventes autres modes', currency_field='currency_id', compute='_compute_totals')
    live_cash = fields.Monetary(string='Espèces actuellement en caisse', currency_field='currency_id', compute='_compute_live_cash')

    @api.depends('order_ids.amount_total', 'order_ids.amount_untaxed', 'order_ids.state', 'order_ids.adk_shop_pay_journal_id',
                 'order_ids.adk_pos_state')
    def _compute_totals(self):
        for sess in self:
            orders = sess.order_ids.filtered(lambda o: o.state in ('sale', 'done') and o.adk_pos_state != 'pending')
            cash = orders.filtered(lambda o: o.adk_shop_pay_journal_id.type == 'cash')
            sess.sales_count = len(orders)
            sess.sales_untaxed = sum(orders.mapped('amount_untaxed'))
            sess.sales_total = sum(orders.mapped('amount_total'))
            sess.cash_sales = sum(cash.mapped('amount_total'))
            sess.other_sales = sess.sales_total - sess.cash_sales

    @api.depends('order_ids.adk_pos_state', 'order_ids.state')
    def _compute_pending(self):
        for sess in self:
            sess.pending_count = len(sess.order_ids.filtered(lambda o: o.adk_pos_state == 'pending' and o.state == 'sale'))

    def _compute_live_cash(self):
        for sess in self:
            sess.live_cash = sess.shop_id._cash_balance_now()

    # ------------------------------------------------------------------ ouverture
    @api.model_create_multi
    def create(self, vals_list):
        records = self.browse()
        for vals in vals_list:
            shop = self.env['adk.shop'].browse(vals.get('shop_id'))
            shop._check_can_sell_pos()
            if self.search_count([('shop_id', '=', shop.id), ('state', '=', 'open')]):
                raise UserError(self.env._("La caisse de « %s » est déjà ouverte. Clôturez d'abord la journée en cours.", shop.name))
            vals = dict(vals)
            counted = vals.pop('opening_cash', 0.0) or 0.0
            vals['name'] = self.env['ir.sequence'].next_by_code('adk.shop.session') or 'Nouveau'
            sess = super().create([vals])
            move = self.env['adk.shop.cash.move'].sudo().create({
                'shop_id': shop.id, 'kind': 'count', 'amount': counted, 'session_id': sess.id,
                'reason': self.env._("Ouverture de caisse %s", sess.name)})
            sess.sudo().write({'opening_cash': counted, 'opening_expected': move.expected,
                               'opening_difference': move.difference})
            records |= sess
        return records

    # ------------------------------------------------------------------ clôture
    def action_close_wizard(self):
        self.ensure_one()
        return {'type': 'ir.actions.act_window', 'name': self.env._('Clôturer la journée'),
                'res_model': 'adk.shop.session.close', 'view_mode': 'form', 'views': [(False, 'form')],
                'target': 'new', 'context': {'default_session_id': self.id}}

    def action_pos_sale(self):
        self.ensure_one()
        return self.shop_id.action_open_pos()

    def _do_close(self, counted, note=False):
        self.ensure_one()
        if self.state != 'open':
            raise UserError(self.env._("Cette journée est déjà clôturée."))
        if self.pending_count:
            raise UserError(self.env._(
                "%s commande(s) sont encore en attente de paiement. Encaissez-les ou annulez-les "
                "(écran de vente, bouton « En attente ») avant de clôturer la journée.", self.pending_count))
        move = self.env['adk.shop.cash.move'].sudo().create({
            'shop_id': self.shop_id.id, 'kind': 'count', 'amount': counted, 'session_id': self.id,
            'reason': self.env._("Clôture de caisse %s", self.name)})
        vals = {'state': 'closed', 'date_close': fields.Datetime.now(), 'close_user_id': self.env.user.id,
                'closing_cash': counted, 'closing_expected': move.expected, 'closing_difference': move.difference}
        if note:
            vals['note'] = ((self.note + '\n') if self.note else '') + note
        self.sudo().write(vals)
        self.message_post(body=self.env._(
            "Journée clôturée : %(n)s vente(s), total %(tot)s, écart de caisse %(diff)s.",
            n=self.sales_count, tot=self.sales_total, diff=self.closing_difference))
        return move

    # ------------------------------------------------------------------ écran de caisse (panier)
    @api.model
    def pos_data(self, session_id):
        """Données de l'écran de vente POS : articles, catégories, modes de paiement, clients."""
        sess = self.browse(session_id).exists()
        if not sess or sess.state != 'open':
            raise UserError(self.env._("Cette caisse n'est pas ouverte."))
        shop = sess.shop_id
        company = shop.company_id
        domain = [('sale_ok', '=', True), ('type', 'in', ('consu', 'service')), '|', ('company_id', '=', False), ('company_id', '=', company.id)]
        assigned = self.env['product.template'].search_count([('adk_shop_ids', 'in', shop.id)])
        if assigned:
            domain.append(('adk_shop_ids', 'in', shop.id))
        products = self.env['product.product'].with_context(warehouse_id=shop.warehouse_id.id).search(domain, limit=600, order='categ_id, name')
        taxes_cache = {}
        items = []
        for p in products:
            taxes = p.taxes_id.filtered(lambda t: t.company_id == company)
            res = taxes.compute_all(p.lst_price, shop.currency_id, 1.0, product=p)
            items.append({'id': p.id, 'name': p.display_name, 'price': res['total_included'], 'categ_id': p.categ_id.id,
                          'stock': p.qty_available if p.is_storable else None, 'has_image': bool(p.image_128)})
        categs = [{'id': c.id, 'name': c.name} for c in products.categ_id.sorted('name')]
        journals = self.env['account.journal'].search([('type', 'in', ('cash', 'bank')), ('company_id', '=', company.id)], order='type desc, name')
        partners = self.env['res.partner'].search([('customer_rank', '>', 0)], limit=200, order='name')
        cur = shop.currency_id
        return {
            'session': {'id': sess.id, 'name': sess.name, 'live_cash': sess.live_cash, 'sales_count': sess.sales_count,
                        'sales_total': sess.sales_total},
            'shop': {'id': shop.id, 'name': shop.name, 'code': shop.code},
            'currency': {'symbol': cur.symbol, 'position': cur.position, 'decimals': cur.decimal_places},
            'products': items, 'categories': categs,
            'journals': [{'id': j.id, 'name': j.name, 'type': j.type} for j in journals],
            'partners': [{'id': pa.id, 'name': pa.name} for pa in partners],
            'user': self.env.user.name,
            'pending': sess._pos_pending_list(),
        }

    def _pos_pending_list(self):
        self.ensure_one()
        out = []
        for o in self.order_ids.filtered(lambda o: o.adk_pos_state == 'pending' and o.state == 'sale').sorted('id', reverse=True):
            out.append({'id': o.id, 'name': o.name, 'total': o.amount_total, 'partner_id': o.partner_id.id,
                        'partner': o.partner_id.name, 'time': fields.Datetime.to_string(o.date_order),
                        'lines': [{'product_id': l.product_id.id, 'qty': l.product_uom_qty}
                                  for l in o.order_line if not l.display_type and l.product_id]})
        return out

    def _pos_wizard(self, lines, journal_id=False, partner_id=False, tendered=0.0, order_id=False):
        self.ensure_one()
        if self.state != 'open':
            raise UserError(self.env._("La caisse est clôturée : ouvrez une nouvelle journée pour vendre."))
        vals = {
            'shop_id': self.shop_id.id, 'session_id': self.id,
            'partner_id': partner_id or False, 'amount_tendered': tendered or 0.0,
            'order_id': order_id or False,
            'line_ids': [Command.create({'product_id': l['product_id'], 'qty': l['qty'],
                                         'price_unit': self.env['product.product'].browse(l['product_id']).lst_price})
                         for l in lines if l.get('qty', 0) > 0]}
        if journal_id:
            vals['journal_id'] = journal_id
        return self.env['adk.shop.pos.sale'].create(vals)

    @api.model
    def pos_order(self, session_id, lines, partner_id=False, order_id=False):
        """« Commander » : enregistre la commande du client sans encaisser ni imprimer (le client paiera ensuite)."""
        sess = self.browse(session_id).exists()
        if not sess:
            raise UserError(self.env._("Journée de caisse introuvable."))
        order = sess._pos_wizard(lines, False, partner_id, 0.0, order_id)._make_order()
        sess.invalidate_recordset()
        return {'order_id': order.id, 'name': order.name, 'total': order.amount_total, 'pending': sess._pos_pending_list()}

    @api.model
    def pos_cancel_order(self, session_id, order_id):
        sess = self.browse(session_id).exists()
        order = self.env['sale.order'].browse(order_id).exists()
        if not sess or not order or order.adk_session_id != sess or order.adk_pos_state != 'pending':
            raise UserError(self.env._("Commande en attente introuvable."))
        order.with_context(disable_cancel_warning=True).action_cancel()
        order.adk_pos_state = False
        sess.invalidate_recordset()
        return sess._pos_pending_list()

    @api.model
    def pos_checkout(self, session_id, lines, journal_id, partner_id=False, tendered=0.0, ticket=False, order_id=False,
                     client_uuid=False):
        """Encaisse le panier : commande, livraison, facture, paiement (et entrée en caisse si espèces).
        Avec ``order_id``, on encaisse une commande déjà enregistrée (« Commander »)."""
        sess = self.browse(session_id).exists()
        if not sess:
            raise UserError(self.env._("Journée de caisse introuvable."))
        if client_uuid:
            done = self.env['sale.order'].search([('adk_client_uuid', '=', client_uuid)], limit=1)
            if done:                                    # déjà synchronisée : on ne recrée rien
                sess.invalidate_recordset()
                return {'order_id': done.id, 'name': done.name, 'total': done.amount_total, 'live_cash': sess.live_cash,
                        'sales_count': sess.sales_count, 'sales_total': sess.sales_total, 'pending': sess._pos_pending_list()}
        wiz = sess._pos_wizard(lines, journal_id, partner_id, tendered, order_id)
        order = wiz._validate()
        if client_uuid:
            order.adk_client_uuid = client_uuid
        sess.invalidate_recordset()
        res = {'order_id': order.id, 'name': order.name, 'total': order.amount_total,
               'live_cash': sess.live_cash, 'sales_count': sess.sales_count, 'sales_total': sess.sales_total,
               'pending': sess._pos_pending_list()}
        if ticket:
            res['ticket'] = self.env.ref('adk_gestion_boutiques.action_report_adk_ticket').report_action(order)
        return res

    def action_print_z(self):
        return self.env.ref('adk_gestion_boutiques.action_report_adk_session').report_action(self)

    # ------------------------------------------------------------------ données du ticket Z
    def _report_payment_lines(self):
        self.ensure_one()
        data = {}
        for o in self.order_ids.filtered(lambda o: o.state in ('sale', 'done') and o.adk_pos_state != 'pending'):
            key = o.adk_shop_pay_journal_id
            row = data.setdefault(key, [key.name or '', 0, 0.0])
            row[1] += 1
            row[2] += o.amount_total
        return sorted(data.values(), key=lambda r: -r[2])

    def _report_product_lines(self):
        self.ensure_one()
        lines = self.env['sale.order.line'].search([('order_id', 'in', self.order_ids.filtered(
            lambda o: o.state in ('sale', 'done') and o.adk_pos_state != 'pending').ids), ('display_type', '=', False)])
        agg = {}
        for l in lines:
            row = agg.setdefault(l.product_id, [l.product_id.display_name, 0.0, 0.0])
            row[1] += l.product_uom_qty
            row[2] += l.price_total
        return sorted(agg.values(), key=lambda r: -r[2])

    def _report_cash_lines(self):
        self.ensure_one()
        rows = [(m.name, m.date, m.reason, m.kind, m.amount) for m in self.cash_move_ids.sorted('id')]
        return rows


class SaleOrder(models.Model):
    _inherit = 'sale.order'

    adk_session_id = fields.Many2one('adk.shop.session', string='Journée de caisse', index=True, copy=False, readonly=True)
    adk_shop_pay_journal_id = fields.Many2one('account.journal', string='Mode de paiement (caisse)', copy=False, readonly=True)
    adk_client_uuid = fields.Char(string='Identifiant de vente (caisse)', copy=False, index=True, readonly=True,
                                  help="Évite tout doublon quand une vente faite hors connexion est synchronisée.")
    adk_pos_state = fields.Selection([('pending', 'En attente de paiement'), ('paid', 'Payée')],
                                     string='Statut caisse', copy=False, readonly=True)


class AdkShopCashMove(models.Model):
    _inherit = 'adk.shop.cash.move'

    session_id = fields.Many2one('adk.shop.session', string='Journée de caisse', index=True, readonly=True)

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if not vals.get('session_id') and vals.get('shop_id'):
                open_sess = self.env['adk.shop.session'].sudo().search(
                    [('shop_id', '=', vals['shop_id']), ('state', '=', 'open')], limit=1)
                if open_sess:
                    vals['session_id'] = open_sess.id
        return super().create(vals_list)
