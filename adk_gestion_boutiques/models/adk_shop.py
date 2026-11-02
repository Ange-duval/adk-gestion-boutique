from datetime import datetime, time, timedelta

from odoo import Command, api, fields, models
from odoo.exceptions import UserError, ValidationError

KPI_PERIODS = [
    ('today', "Aujourd'hui"),
    ('week', '7 derniers jours'),
    ('month', 'Mois en cours'),
    ('last_month', 'Mois précédent'),
    ('quarter', 'Trimestre en cours'),
    ('year', 'Année en cours'),
]


class AdkShop(models.Model):
    _name = 'adk.shop'
    _description = 'Boutique'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _parent_name = 'parent_id'
    _parent_store = True
    _rec_names_search = ['name', 'code']
    _order = 'company_id, code'

    # --- Identité -----------------------------------------------------------
    name = fields.Char(string='Nom commercial', required=True, tracking=True)
    code = fields.Char(string='Code unique', required=True, copy=False, tracking=True)
    image_1920 = fields.Image(string='Logo / photo', max_width=1920, max_height=1920)
    image_128 = fields.Image(string='Logo (miniature)', related='image_1920', max_width=128, max_height=128, store=True)
    active = fields.Boolean(default=True, tracking=True)
    state = fields.Selection(
        [('draft', 'Brouillon'), ('active', 'Active'), ('closed', 'Clôturée')],
        string='État', default='draft', required=True, tracking=True, copy=False)
    activity_type = fields.Selection(
        [('retail', 'Vente au détail'), ('wholesale', 'Vente en gros'),
         ('mixed', 'Détail et gros'), ('restaurant', 'Restauration'),
         ('services', 'Services'), ('other', 'Autre')],
        string="Type d'activité", default='retail')
    sector = fields.Char(string='Secteur commercial')
    description = fields.Html(string='Description')

    # --- Coordonnées --------------------------------------------------------
    street = fields.Char(string='Adresse')
    street2 = fields.Char(string='Complément')
    city = fields.Char(string='Ville')
    zip = fields.Char(string='Code postal')
    state_id = fields.Many2one('res.country.state', string='Région', domain="[('country_id', '=?', country_id)]")
    country_id = fields.Many2one('res.country', string='Pays')
    phone = fields.Char(string='Téléphone')
    email = fields.Char(string='Email')
    website = fields.Char(string='Site web')

    # --- Organisation -------------------------------------------------------
    company_id = fields.Many2one(
        'res.company', string='Société propriétaire', required=True,
        default=lambda self: self.env.company, index=True, tracking=True)
    currency_id = fields.Many2one(related='company_id.currency_id', string='Devise', store=True)
    group_id = fields.Many2one('adk.shop.group', string='Groupe / région', index=True, tracking=True)
    parent_id = fields.Many2one('adk.shop', string='Boutique mère', index=True, ondelete='restrict')
    parent_path = fields.Char(index=True)
    child_ids = fields.One2many('adk.shop', 'parent_id', string='Boutiques filles')
    child_count = fields.Integer(compute='_compute_child_count')
    is_subsidiary = fields.Boolean(
        string='Filiale (incluse dans les rapports de groupe)',
        help="Cochez si la boutique est une filiale devant figurer dans les rapports consolidés de sa boutique mère.")
    manager_id = fields.Many2one('res.users', string='Responsable', domain="[('share', '=', False)]", tracking=True)
    user_ids = fields.Many2many(
        'res.users', 'adk_shop_user_rel', 'shop_id', 'user_id',
        string='Équipe autorisée', domain="[('share', '=', False)]")
    warehouse_id = fields.Many2one(
        'stock.warehouse', string='Entrepôt',
        domain="[('company_id', '=', company_id)]", check_company=True)

    # --- Modes de vente -----------------------------------------------------
    sale_mode = fields.Selection(
        [('both', 'Vente POS (caisse) et vente simple'),
         ('simple', 'Vente simple uniquement'),
         ('pos', 'Vente POS (caisse) uniquement')],
        string='Mode de vente', default='both', required=True, tracking=True,
        help="Vente simple : devis, commande, livraison, facture et paiement comme dans l'application Ventes. "
             "Vente POS : caisse de la boutique, avec ouverture de la journée le matin, ventes comptoir "
             "rapides (encaissement immédiat), puis clôture de la journée et ticket Z le soir.")
    has_pos = fields.Boolean(compute='_compute_has_pos', string='Caisse disponible')
    session_ids = fields.One2many('adk.shop.session', 'shop_id', string='Journées de caisse')
    session_count = fields.Integer(compute='_compute_session', string='Journées de caisse')
    open_session_id = fields.Many2one('adk.shop.session', compute='_compute_session', string='Journée en cours')
    pos_open = fields.Boolean(compute='_compute_session', string='Caisse ouverte')

    # --- Fiscalité ----------------------------------------------------------
    fiscal_regime = fields.Selection(
        [('real', 'Régime du réel'), ('simplified', 'Régime simplifié'),
         ('liberatory', 'Impôt libératoire'), ('exempt', 'Exonéré'), ('other', 'Autre')],
        string='Régime fiscal', tracking=True)
    tax_id_number = fields.Char(string='Identifiant fiscal (NIU / TVA)')
    trade_register = fields.Char(string='Registre de commerce')
    sale_tax_ids = fields.Many2many(
        'account.tax', 'adk_shop_sale_tax_rel', 'shop_id', 'tax_id',
        string='Taxes de vente', domain="[('type_tax_use', '=', 'sale'), ('company_id', '=', company_id)]")
    purchase_tax_ids = fields.Many2many(
        'account.tax', 'adk_shop_purchase_tax_rel', 'shop_id', 'tax_id',
        string="Taxes d'achat", domain="[('type_tax_use', '=', 'purchase'), ('company_id', '=', company_id)]")

    # --- Contrôle des dépenses ---------------------------------------------
    expense_auto_approve_limit = fields.Monetary(
        string="Seuil d'approbation automatique", currency_field='currency_id', tracking=True,
        help="Les dépenses d'un montant inférieur ou égal à ce seuil sont approuvées automatiquement. "
             "0 = toute dépense doit être approuvée par un responsable.")

    # --- Intégration native -------------------------------------------------
    tag_id = fields.Many2one('res.partner.category', string='Étiquette', copy=False, ondelete='set null')

    # --- Contenu de l'espace boutique (onglets) -----------------------------
    sale_order_ids = fields.One2many('sale.order', 'adk_shop_id', string='Ventes')
    invoice_ids = fields.One2many('account.move', 'adk_shop_id', string='Factures clients',
                                  domain=[('move_type', 'in', ('out_invoice', 'out_refund'))])
    payment_in_ids = fields.One2many('account.payment', 'adk_shop_id', string='Encaissements',
                                     domain=[('payment_type', '=', 'inbound')])
    income_ids = fields.One2many('adk.shop.income', 'shop_id', string='Autres revenus')
    purchase_order_ids = fields.One2many('purchase.order', 'adk_shop_id', string='Achats')
    bill_ids = fields.One2many('account.move', 'adk_shop_id', string='Factures fournisseurs',
                               domain=[('move_type', 'in', ('in_invoice', 'in_refund'))])
    payment_out_ids = fields.One2many('account.payment', 'adk_shop_id', string='Paiements fournisseurs',
                                      domain=[('payment_type', '=', 'outbound')])
    expense_ids = fields.One2many('adk.shop.expense', 'shop_id', string='Dépenses')
    budget_ids = fields.One2many('adk.shop.budget', 'shop_id', string='Budgets')
    cash_move_ids = fields.One2many('adk.shop.cash.move', 'shop_id', string='Mouvements de caisse')
    alert_ids = fields.One2many('adk.shop.alert', 'shop_id', string='Alertes')
    open_alert_ids = fields.One2many('adk.shop.alert', 'shop_id', string='Alertes à traiter',
                                     domain=[('state', 'in', ('new', 'ack'))])
    transfer_out_ids = fields.One2many('adk.shop.transfer', 'shop_from_id', string='Transferts envoyés')
    transfer_in_ids = fields.One2many('adk.shop.transfer', 'shop_to_id', string='Transferts reçus')
    picking_ids = fields.One2many('stock.picking', 'adk_shop_id', string='Réceptions et livraisons')
    stock_move_ids = fields.One2many('stock.move', 'adk_shop_id', string='Mouvements de stock')
    product_tmpl_ids = fields.Many2many('product.template', 'adk_shop_product_tmpl_rel', 'shop_id',
                                        'product_tmpl_id', string='Produits')
    customer_ids = fields.Many2many('res.partner', compute='_compute_partner_lists', string='Clients')
    supplier_ids = fields.Many2many('res.partner', compute='_compute_partner_lists', string='Fournisseurs')
    quant_ids = fields.Many2many('stock.quant', compute='_compute_quant_ids', string='Stock actuel')

    customer_count = fields.Integer(compute='_compute_counts')
    supplier_count = fields.Integer(compute='_compute_counts')
    product_count = fields.Integer(compute='_compute_counts')
    expense_count = fields.Integer(compute='_compute_counts')
    alert_count = fields.Integer(compute='_compute_counts', string='Alertes ouvertes')

    # --- Indicateurs (calculés depuis les documents réels) ------------------
    kpi_period = fields.Selection(KPI_PERIODS, string='Période', default='month', required=True)
    kpi_label = fields.Char(compute='_compute_kpis', string='Période affichée')
    kpi_revenue = fields.Monetary(compute='_compute_kpis', currency_field='currency_id', string="Chiffre d'affaires HT",
                                  help="Factures clients comptabilisées sur la période, hors taxes, avoirs déduits.")
    kpi_cogs = fields.Monetary(compute='_compute_kpis', currency_field='currency_id', string='Coût des marchandises vendues',
                               help="Quantités vendues (factures clients comptabilisées) × coût actuel du produit.")
    kpi_margin = fields.Monetary(compute='_compute_kpis', currency_field='currency_id', string='Marge brute estimée',
                                 help="Chiffre d'affaires HT − coût des marchandises vendues. Estimation de gestion fondée sur le coût actuel des produits.")
    kpi_charges = fields.Monetary(compute='_compute_kpis', currency_field='currency_id', string="Charges d'exploitation HT",
                                  help="Factures fournisseurs comptabilisées sur la période (hors achats de marchandises stockées), "
                                       "hors taxes, avoirs déduits.")
    kpi_goods_purchases = fields.Monetary(compute='_compute_kpis', currency_field='currency_id', string='Achats de marchandises HT',
                                          help="Achats de produits suivis en stock sur la période : ils alimentent le stock, ils ne sont pas une charge.")
    kpi_income_pending = fields.Monetary(compute='_compute_kpis', currency_field='currency_id',
                                         string='Autres revenus non facturés')
    kpi_expenses_pending = fields.Monetary(compute='_compute_kpis', currency_field='currency_id',
                                           string='Dépenses approuvées non facturées')
    kpi_result = fields.Monetary(compute='_compute_kpis', currency_field='currency_id', string='Résultat estimé',
                                 help="Estimation de gestion = marge brute + autres revenus non facturés − charges d'exploitation − "
                                      "dépenses approuvées non facturées. Ce n'est PAS le résultat comptable.")
    kpi_collected = fields.Monetary(compute='_compute_kpis', currency_field='currency_id', string='Encaissé',
                                    help="Paiements clients enregistrés sur la période.")
    kpi_paid_out = fields.Monetary(compute='_compute_kpis', currency_field='currency_id', string='Décaissé',
                                   help="Paiements fournisseurs enregistrés sur la période.")
    kpi_receivable = fields.Monetary(compute='_compute_kpis', currency_field='currency_id', string='Créances clients')
    kpi_payable = fields.Monetary(compute='_compute_kpis', currency_field='currency_id', string='Dettes fournisseurs')
    kpi_overdue_count = fields.Integer(compute='_compute_kpis', string='Factures en retard')
    kpi_sales_count = fields.Integer(compute='_compute_kpis', string='Commandes de vente')
    kpi_stock_value = fields.Monetary(compute='_compute_kpis', currency_field='currency_id', string='Valeur du stock',
                                      help="Quantités en stock de l'entrepôt × coût du produit.")
    cash_balance = fields.Monetary(compute='_compute_cash_balance', currency_field='currency_id', string='Solde de caisse')

    _sql_constraints = [
        ('code_company_uniq', 'unique(code, company_id)', "Ce code de boutique existe déjà dans cette société."),
        ('name_company_uniq', 'unique(name, company_id)', "Ce nom de boutique existe déjà dans cette société."),
    ]

    # ------------------------------------------------------------------------
    # Compute / contraintes
    # ------------------------------------------------------------------------
    @api.depends('name', 'code')
    def _compute_display_name(self):
        for shop in self:
            shop.display_name = '[%s] %s' % (shop.code, shop.name) if shop.code else (shop.name or '')

    @api.depends('child_ids')
    def _compute_child_count(self):
        data = self._read_group([('parent_id', 'in', self.ids)], ['parent_id'], ['__count'])
        mapped = {shop.id: count for shop, count in data}
        for shop in self:
            shop.child_count = mapped.get(shop.id, 0)

    def _compute_has_pos(self):
        for shop in self:
            shop.has_pos = shop.sale_mode != 'simple'

    @api.depends('session_ids.state')
    def _compute_session(self):
        Session = self.env['adk.shop.session'].sudo()
        counts = {shop.id: n for shop, n in Session._read_group([('shop_id', 'in', self.ids)], ['shop_id'], ['__count'])}
        opened = {sess.shop_id.id: sess for sess in Session.search([('shop_id', 'in', self.ids), ('state', '=', 'open')])}
        for shop in self:
            shop.session_count = counts.get(shop.id, 0)
            shop.open_session_id = opened.get(shop.id, False)
            shop.pos_open = shop.id in opened

    def _cash_balance_now(self):
        self.ensure_one()
        last = self.env['adk.shop.cash.move'].sudo().search([('shop_id', '=', self.id)], order='id desc', limit=1)
        return last.balance_after if last else 0.0

    def _check_can_sell_pos(self):
        self._ensure_warehouse()
        for shop in self:
            if shop.state != 'active':
                raise UserError(self.env._("La boutique « %s » n'est pas active.", shop.name))
            if shop.sale_mode == 'simple':
                raise UserError(self.env._(
                    "La boutique « %s » est en « Vente simple uniquement ». Changez son mode de vente "
                    "(onglet Équipe & Paramètres) pour utiliser la caisse.", shop.name))

    def _compute_counts(self):
        Partner = self.env['res.partner']
        cust = {s.id: c for s, c in Partner._read_group(
            [('adk_shop_ids', 'in', self.ids), ('customer_rank', '>', 0)], ['adk_shop_ids'], ['__count'])}
        supp = {s.id: c for s, c in Partner._read_group(
            [('adk_shop_ids', 'in', self.ids), ('supplier_rank', '>', 0)], ['adk_shop_ids'], ['__count'])}
        prod = {s.id: c for s, c in self.env['product.template']._read_group(
            [('adk_shop_ids', 'in', self.ids)], ['adk_shop_ids'], ['__count'])}
        exp = {s.id: c for s, c in self.env['adk.shop.expense']._read_group(
            [('shop_id', 'in', self.ids)], ['shop_id'], ['__count'])}
        alr = {s.id: c for s, c in self.env['adk.shop.alert']._read_group(
            [('shop_id', 'in', self.ids), ('state', 'in', ('new', 'ack'))], ['shop_id'], ['__count'])}
        for shop in self:
            shop.customer_count = cust.get(shop.id, 0)
            shop.supplier_count = supp.get(shop.id, 0)
            shop.product_count = prod.get(shop.id, 0)
            shop.expense_count = exp.get(shop.id, 0)
            shop.alert_count = alr.get(shop.id, 0)

    def _compute_partner_lists(self):
        Partner = self.env['res.partner']
        for shop in self:
            if isinstance(shop.id, int):
                shop.customer_ids = Partner.search(
                    [('adk_shop_ids', 'in', shop.id), ('customer_rank', '>', 0)], limit=200)
                shop.supplier_ids = Partner.search(
                    [('adk_shop_ids', 'in', shop.id), ('supplier_rank', '>', 0)], limit=200)
            else:
                shop.customer_ids = shop.supplier_ids = False

    def _compute_quant_ids(self):
        Quant = self.env['stock.quant'].sudo()
        for shop in self:
            if isinstance(shop.id, int) and shop.warehouse_id:
                shop.quant_ids = Quant.search(
                    [('location_id', 'child_of', shop.warehouse_id.view_location_id.id),
                     ('location_id.usage', '=', 'internal'), ('quantity', '!=', 0)], limit=300)
            else:
                shop.quant_ids = False

    @api.constrains('parent_id')
    def _check_parent_recursion(self):
        if self._has_cycle():
            raise ValidationError(self.env._("Une boutique ne peut pas être sa propre boutique mère."))

    @api.constrains('company_id', 'warehouse_id', 'sale_tax_ids', 'purchase_tax_ids')
    def _check_company_consistency(self):
        for shop in self:
            if shop.warehouse_id and shop.warehouse_id.company_id != shop.company_id:
                raise ValidationError(self.env._("L'entrepôt doit appartenir à la société de la boutique."))
            taxes = shop.sale_tax_ids | shop.purchase_tax_ids
            if any(t.company_id != shop.company_id for t in taxes):
                raise ValidationError(self.env._("Les taxes doivent appartenir à la société de la boutique."))

    @api.constrains('warehouse_id')
    def _check_warehouse_unique(self):
        for shop in self.filtered('warehouse_id'):
            if self.with_context(active_test=False).search_count(
                    [('warehouse_id', '=', shop.warehouse_id.id), ('id', '!=', shop.id)]):
                raise ValidationError(self.env._("Un entrepôt ne peut être rattaché qu'à une seule boutique."))

    # ------------------------------------------------------------------------
    # Indicateurs
    # ------------------------------------------------------------------------
    @api.model
    def _period_range(self, period, today=None):
        today = today or fields.Date.context_today(self)
        if period == 'today':
            return today, today
        if period == 'week':
            return today - timedelta(days=6), today
        if period == 'last_month':
            end = today.replace(day=1) - timedelta(days=1)
            return end.replace(day=1), end
        if period == 'quarter':
            return today.replace(month=3 * ((today.month - 1) // 3) + 1, day=1), today
        if period == 'year':
            return today.replace(month=1, day=1), today
        return today.replace(day=1), today

    def _get_period_kpis(self, date_from, date_to):
        """Indicateurs calculés depuis les documents natifs (lecture agrégée, sans montant saisi à la main)."""
        today = fields.Date.context_today(self)
        shops = self.filtered(lambda s: isinstance(s.id, int))
        res = {s.id: {
            'revenue': 0.0, 'cogs': 0.0, 'margin': 0.0, 'charges': 0.0, 'goods_purchases': 0.0,
            'expenses_pending': 0.0, 'income_pending': 0.0,
            'collected': 0.0, 'paid_out': 0.0, 'receivable': 0.0, 'payable': 0.0,
            'overdue_count': 0, 'overdue_amount': 0.0, 'sales_count': 0, 'stock_value': 0.0,
        } for s in shops}
        if not shops:
            return res
        Move = self.env['account.move'].sudo()
        base = [('adk_shop_id', 'in', shops.ids), ('state', '=', 'posted')]
        period = [('invoice_date', '>=', date_from), ('invoice_date', '<=', date_to)]
        sales = ('out_invoice', 'out_refund')
        purchases = ('in_invoice', 'in_refund')
        for shop, total in Move._read_group(
                base + period + [('move_type', 'in', sales)], ['adk_shop_id'], ['amount_untaxed_signed:sum']):
            res[shop.id]['revenue'] = total
        Line = self.env['account.move.line'].sudo()
        line_base = [('adk_shop_id', 'in', shops.ids), ('parent_state', '=', 'posted'), ('display_type', '=', 'product'),
                     ('move_id.invoice_date', '>=', date_from), ('move_id.invoice_date', '<=', date_to)]
        for shop, product, mtype, qty in Line._read_group(
                line_base + [('move_type', 'in', sales), ('product_id.is_storable', '=', True)],
                ['adk_shop_id', 'product_id', 'move_type'], ['quantity:sum']):
            sign = 1 if mtype == 'out_invoice' else -1
            res[shop.id]['cogs'] += sign * qty * product.with_company(shop.company_id).standard_price
        for shop, total in Line._read_group(
                line_base + [('move_type', 'in', purchases), '|', ('product_id', '=', False), ('product_id.is_storable', '=', False)],
                ['adk_shop_id'], ['balance:sum']):
            res[shop.id]['charges'] = total
        for shop, total in Line._read_group(
                line_base + [('move_type', 'in', purchases), ('product_id.is_storable', '=', True)],
                ['adk_shop_id'], ['balance:sum']):
            res[shop.id]['goods_purchases'] = total
        unpaid = base + [('payment_state', 'in', ('not_paid', 'partial'))]
        for shop, total in Move._read_group(
                unpaid + [('move_type', 'in', sales)], ['adk_shop_id'], ['amount_residual_signed:sum']):
            res[shop.id]['receivable'] = total
        for shop, total in Move._read_group(
                unpaid + [('move_type', 'in', purchases)], ['adk_shop_id'], ['amount_residual_signed:sum']):
            res[shop.id]['payable'] = -total
        for shop, count, total in Move._read_group(
                unpaid + [('move_type', '=', 'out_invoice'), ('invoice_date_due', '<', today)],
                ['adk_shop_id'], ['__count', 'amount_residual_signed:sum']):
            res[shop.id]['overdue_count'] = count
            res[shop.id]['overdue_amount'] = total
        pay_dom = [('adk_shop_id', 'in', shops.ids), ('state', 'in', ('in_process', 'paid')),
                   ('date', '>=', date_from), ('date', '<=', date_to)]
        Payment = self.env['account.payment'].sudo()
        for shop, total in Payment._read_group(
                pay_dom + [('payment_type', '=', 'inbound')], ['adk_shop_id'], ['amount_company_currency_signed:sum']):
            res[shop.id]['collected'] = total
        for shop, total in Payment._read_group(
                pay_dom + [('payment_type', '=', 'outbound')], ['adk_shop_id'], ['amount_company_currency_signed:sum']):
            res[shop.id]['paid_out'] = -total
        dt_from = datetime.combine(date_from, time.min)
        dt_to = datetime.combine(date_to, time.max)
        for shop, count in self.env['sale.order'].sudo()._read_group(
                [('adk_shop_id', 'in', shops.ids), ('state', '=', 'sale'),
                 ('date_order', '>=', dt_from), ('date_order', '<=', dt_to)], ['adk_shop_id'], ['__count']):
            res[shop.id]['sales_count'] = count
        for model, key, field_name in (('adk.shop.expense', 'expenses_pending', 'approved'),
                                       ('adk.shop.income', 'income_pending', 'confirmed')):
            for shop, total in self.env[model].sudo()._read_group(
                    [('shop_id', 'in', shops.ids), ('state', '=', field_name),
                     ('date', '>=', date_from), ('date', '<=', date_to)], ['shop_id'], ['amount:sum']):
                res[shop.id][key] = total
        for shop_id, value in shops._get_stock_value().items():
            res[shop_id]['stock_value'] = value
        for vals in res.values():
            vals['margin'] = vals['revenue'] - vals['cogs']
            vals['result'] = (vals['margin'] + vals['income_pending']
                              - vals['charges'] - vals['expenses_pending'])
        return res

    def _get_stock_value(self):
        """Valeur du stock interne de l'entrepôt de chaque boutique (quantité × coût du produit)."""
        Quant = self.env['stock.quant'].sudo()
        res = {}
        for shop in self:
            value = 0.0
            if shop.warehouse_id:
                rows = Quant._read_group(
                    [('location_id', 'child_of', shop.warehouse_id.view_location_id.id),
                     ('location_id.usage', '=', 'internal')],
                    ['product_id'], ['quantity:sum'])
                for product, qty in rows:
                    value += qty * product.with_company(shop.company_id).standard_price
            res[shop.id] = value
        return res

    @api.depends('kpi_period')
    def _compute_kpis(self):
        # Les montants dépendent de la période choisie ; regroupement par période pour limiter les requêtes.
        for period in set(self.mapped('kpi_period')):
            shops = self.filtered(lambda s: s.kpi_period == period)
            date_from, date_to = self._period_range(period)
            data = shops._get_period_kpis(date_from, date_to)
            label = "%s → %s" % (fields.Date.to_string(date_from), fields.Date.to_string(date_to))
            for shop in shops:
                d = data.get(shop.id, {})
                shop.kpi_label = label
                shop.kpi_revenue = d.get('revenue', 0.0)
                shop.kpi_cogs = d.get('cogs', 0.0)
                shop.kpi_margin = d.get('margin', 0.0)
                shop.kpi_charges = d.get('charges', 0.0)
                shop.kpi_goods_purchases = d.get('goods_purchases', 0.0)
                shop.kpi_income_pending = d.get('income_pending', 0.0)
                shop.kpi_expenses_pending = d.get('expenses_pending', 0.0)
                shop.kpi_result = d.get('result', 0.0)
                shop.kpi_collected = d.get('collected', 0.0)
                shop.kpi_paid_out = d.get('paid_out', 0.0)
                shop.kpi_receivable = d.get('receivable', 0.0)
                shop.kpi_payable = d.get('payable', 0.0)
                shop.kpi_overdue_count = d.get('overdue_count', 0)
                shop.kpi_sales_count = d.get('sales_count', 0)
                shop.kpi_stock_value = d.get('stock_value', 0.0)

    def _compute_cash_balance(self):
        Move = self.env['adk.shop.cash.move'].sudo()
        for shop in self:
            last = Move.search([('shop_id', '=', shop.id)], order='id desc', limit=1) if isinstance(shop.id, int) else Move
            shop.cash_balance = last.balance_after if last else 0.0

    @api.model
    def get_dashboard_data(self, period='month'):
        """Données du tableau de bord réseau (boutiques actives visibles par l'utilisateur)."""
        today = fields.Date.context_today(self)
        date_from, date_to = self._period_range(period, today)
        span = (date_to - date_from).days + 1
        prev_to = date_from - timedelta(days=1)
        prev_from = prev_to - timedelta(days=span - 1)
        shops = self.search([('state', '=', 'active')], order='code')
        data = shops._get_period_kpis(date_from, date_to)
        prev = shops._get_period_kpis(prev_from, prev_to)
        currencies = shops.mapped('currency_id')
        single = len(currencies) == 1
        keys = ('revenue', 'margin', 'charges', 'goods_purchases', 'result', 'collected', 'paid_out',
                'receivable', 'payable', 'stock_value', 'expenses_pending', 'income_pending')
        totals = {k: sum(d[k] for d in data.values()) for k in keys} if single else {}
        totals['sales_count'] = sum(d['sales_count'] for d in data.values())
        totals['overdue_count'] = sum(d['overdue_count'] for d in data.values())
        prev_revenue = sum(d['revenue'] for d in prev.values()) if single else 0.0
        totals['revenue_prev'] = prev_revenue
        totals['revenue_delta'] = ((totals.get('revenue', 0.0) - prev_revenue) / prev_revenue * 100.0) if prev_revenue else None
        alerts_by_shop = {s.id: c for s, c in self.env['adk.shop.alert']._read_group(
            [('shop_id', 'in', shops.ids), ('state', 'in', ('new', 'ack'))], ['shop_id'], ['__count'])}
        cash = shops._compute_cash_map()
        rows = []
        for shop in shops:
            d = data.get(shop.id, {})
            rows.append({
                'id': shop.id, 'name': shop.name, 'code': shop.code, 'city': shop.city or '',
                'group': shop.group_id.name or '', 'manager': shop.manager_id.name or '',
                'has_image': bool(shop.image_128), 'sale_mode': shop.sale_mode, 'has_pos': shop.has_pos,
                'pos_open': shop.pos_open,
                'currency_id': shop.currency_id.id, 'alerts': alerts_by_shop.get(shop.id, 0),
                'cash_balance': cash.get(shop.id, 0.0),
                **{k: d.get(k, 0.0) for k in keys}, 'sales_count': d.get('sales_count', 0),
                'overdue_count': d.get('overdue_count', 0)})
        # courbe du chiffre d'affaires (par jour, ou par mois sur un trimestre / une année)
        Move = self.env['account.move'].sudo()
        gran = 'month' if period in ('quarter', 'year') else 'day'
        series = {}
        for day, total in Move._read_group(
                [('adk_shop_id', 'in', shops.ids), ('state', '=', 'posted'), ('move_type', 'in', ('out_invoice', 'out_refund')),
                 ('invoice_date', '>=', date_from), ('invoice_date', '<=', date_to)],
                ['invoice_date:%s' % gran], ['amount_untaxed_signed:sum']):
            series[day] = total
        points, cursor = [], date_from.replace(day=1) if gran == 'month' else date_from
        while cursor <= date_to:
            points.append({'label': cursor.strftime('%d/%m') if gran == 'day' else cursor.strftime('%m/%Y'),
                           'value': series.get(cursor, 0.0)})
            cursor = (cursor + timedelta(days=32)).replace(day=1) if gran == 'month' else cursor + timedelta(days=1)
        # meilleures ventes
        Line = self.env['account.move.line'].sudo()
        top = []
        for product, qty, amount in Line._read_group(
                [('adk_shop_id', 'in', shops.ids), ('parent_state', '=', 'posted'), ('display_type', '=', 'product'),
                 ('move_type', '=', 'out_invoice'), ('product_id', '!=', False),
                 ('move_id.invoice_date', '>=', date_from), ('move_id.invoice_date', '<=', date_to)],
                ['product_id'], ['quantity:sum', 'price_subtotal:sum'], order='price_subtotal:sum desc', limit=6):
            top.append({'name': product.display_name, 'qty': qty, 'amount': amount})
        # dépenses par catégorie
        expenses = [{'name': cat.name, 'amount': total} for cat, total in self.env['adk.shop.expense'].sudo()._read_group(
            [('shop_id', 'in', shops.ids), ('state', 'in', ('approved', 'billed')),
             ('date', '>=', date_from), ('date', '<=', date_to)],
            ['category_id'], ['amount:sum'], order='amount:sum desc', limit=6)]
        alerts = [{'id': a.id, 'name': a.name, 'shop': a.shop_id.code, 'severity': a.severity, 'type': a.alert_type}
                  for a in self.env['adk.shop.alert'].search([('shop_id', 'in', shops.ids), ('state', 'in', ('new', 'ack'))], limit=7)]
        return {
            'period': period, 'date_from': fields.Date.to_string(date_from), 'date_to': fields.Date.to_string(date_to),
            'currency_id': currencies[:1].id if single else False, 'single_currency': single,
            'totals': totals, 'shops': rows, 'series': points, 'top_products': top, 'expenses': expenses,
            'alerts': alerts, 'company': self.env.company.name,
        }

    def _compute_cash_map(self):
        Move = self.env['adk.shop.cash.move'].sudo()
        res = {}
        for shop in self:
            last = Move.search([('shop_id', '=', shop.id)], order='id desc', limit=1)
            res[shop.id] = last.balance_after if last else 0.0
        return res

    @api.model
    def _get_default_shop(self):
        """Boutique par défaut de l'utilisateur : celle où il travaille, s'il n'en a qu'une."""
        shops = self.search([('state', '=', 'active'), '|',
                             ('user_ids', 'in', self.env.uid), ('manager_id', '=', self.env.uid)], limit=2)
        return shops.id if len(shops) == 1 else False

    # ------------------------------------------------------------------------
    # CRUD : étiquette native « Boutique XXX »
    # ------------------------------------------------------------------------
    def _tag_name(self):
        self.ensure_one()
        return self.env._("Boutique %s", self.name)

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('code'):
                vals['code'] = vals['code'].strip().upper()
        shops = super().create(vals_list)
        for shop in shops:
            shop.tag_id = self.env['res.partner.category'].sudo().create({'name': shop._tag_name()})
        shops._ensure_warehouse()
        shops._link_warehouse_pickings()
        return shops

    def write(self, vals):
        if vals.get('code'):
            vals['code'] = vals['code'].strip().upper()
        res = super().write(vals)
        if 'name' in vals:
            for shop in self.filtered('tag_id'):
                shop.tag_id.sudo().name = shop._tag_name()
        if 'warehouse_id' in vals:
            self._link_warehouse_pickings()
        return res

    def _ensure_warehouse(self):
        """Une boutique doit toujours avoir un entrepôt (nécessaire aux commandes, livraisons et au stock).
        On reprend d'abord un entrepôt libre de la société (celui créé nativement par Odoo), sinon on en crée un."""
        Warehouse = self.env['stock.warehouse'].sudo()
        for shop in self.filtered(lambda s: not s.warehouse_id and s.company_id):
            used = self.env['adk.shop'].sudo().search([('warehouse_id', '!=', False)]).warehouse_id
            free = Warehouse.search([('company_id', '=', shop.company_id.id), ('id', 'not in', used.ids)], order='id', limit=1)
            if not free:
                code = ''.join(ch for ch in (shop.code or shop.name or 'BTQ').upper() if ch.isalnum())[:5] or 'BTQ'
                if Warehouse.search_count([('company_id', '=', shop.company_id.id), ('code', '=', code)]):
                    code = (code[:3] + str(shop.id % 100).zfill(2))[:5]
                free = Warehouse.create({'name': shop.name, 'code': code, 'company_id': shop.company_id.id})
            shop.sudo().warehouse_id = free
        return True

    def _link_warehouse_pickings(self):
        """Rattache aux boutiques les mouvements d'entrepôt existants quand un entrepôt est associé."""
        Picking = self.env['stock.picking'].sudo()
        for shop in self.filtered('warehouse_id'):
            pickings = Picking.search([('picking_type_id.warehouse_id', '=', shop.warehouse_id.id)])
            self.env.add_to_compute(Picking._fields['adk_shop_id'], pickings)

    def unlink(self):
        tags = self.tag_id
        res = super().unlink()
        tags.sudo().unlink()
        return res

    # ------------------------------------------------------------------------
    # Actions d'état
    # ------------------------------------------------------------------------
    def action_activate(self):
        self._ensure_warehouse()
        self.write({'state': 'active'})

    def action_close(self):
        self.write({'state': 'closed'})

    def action_draft(self):
        self.write({'state': 'draft'})

    # ------------------------------------------------------------------------
    # Navigation dans l'espace boutique
    # ------------------------------------------------------------------------
    def _shop_context(self, **extra):
        self.ensure_one()
        ctx = {'default_adk_shop_id': self.id, 'default_company_id': self.company_id.id}
        ctx.update(extra)
        return ctx

    def _open_form(self, name, model, context):
        self.ensure_one()
        return {'type': 'ir.actions.act_window', 'name': name, 'res_model': model, 'view_mode': 'form',
                'views': [(False, 'form')], 'target': 'current', 'context': context}

    def _open_list(self, name, model, domain, context, view_mode='list,form'):
        self.ensure_one()
        return {'type': 'ir.actions.act_window', 'name': name, 'res_model': model, 'view_mode': view_mode,
                'domain': domain, 'context': context}

    # -- Créer ---------------------------------------------------------------
    def action_new_sale(self):
        """Vente simple : devis / commande comme dans l'application Ventes, déjà rattaché à la boutique."""
        self._ensure_warehouse()
        ctx = self._shop_context()
        if self.warehouse_id:
            ctx['default_warehouse_id'] = self.warehouse_id.id
        return self._open_form(self.env._('Nouvelle vente'), 'sale.order', ctx)

    def action_open_pos(self):
        """Vente POS : ouvre la vente comptoir si la caisse est ouverte, sinon propose l'ouverture de la journée."""
        self.ensure_one()
        self._check_can_sell_pos()
        sess = self.env['adk.shop.session'].sudo().search([('shop_id', '=', self.id), ('state', '=', 'open')], limit=1)
        if sess:
            return {'type': 'ir.actions.client', 'tag': 'adk_shop_cart', 'name': self.env._('Vente POS — %s', self.name),
                    'params': {'session_id': sess.id}, 'context': {'session_id': sess.id}}
        return self.action_open_session()

    def action_open_session(self):
        self.ensure_one()
        self._check_can_sell_pos()
        return {'type': 'ir.actions.act_window', 'name': self.env._('Ouvrir la caisse — %s', self.name),
                'res_model': 'adk.shop.session.open', 'view_mode': 'form', 'views': [(False, 'form')],
                'target': 'new', 'context': {'default_shop_id': self.id}}

    def action_close_session(self):
        self.ensure_one()
        sess = self.env['adk.shop.session'].sudo().search([('shop_id', '=', self.id), ('state', '=', 'open')], limit=1)
        if not sess:
            raise UserError(self.env._("La caisse de « %s » n'est pas ouverte.", self.name))
        return sess.action_close_wizard()

    def action_view_shop(self):
        self.ensure_one()
        return {'type': 'ir.actions.act_window', 'name': self.display_name, 'res_model': 'adk.shop',
                'res_id': self.id, 'view_mode': 'form', 'views': [(False, 'form')]}

    def action_view_sessions(self):
        return self._open_list(self.env._('Journées de caisse'), 'adk.shop.session',
                               [('shop_id', '=', self.id)], {'default_shop_id': self.id})

    def action_new_purchase(self):
        ctx = self._shop_context()
        if self.warehouse_id and self.warehouse_id.in_type_id:
            ctx['default_picking_type_id'] = self.warehouse_id.in_type_id.id
        return self._open_form(self.env._("Nouvel achat"), 'purchase.order', ctx)

    def action_new_customer_invoice(self):
        return self._open_form(self.env._('Nouvelle facture client'), 'account.move',
                               self._shop_context(default_move_type='out_invoice'))

    def action_new_vendor_bill(self):
        return self._open_form(self.env._('Nouvelle facture fournisseur'), 'account.move',
                               self._shop_context(default_move_type='in_invoice'))

    def action_new_expense(self):
        return self._open_form(self.env._('Nouvelle dépense'), 'adk.shop.expense', {'default_shop_id': self.id})

    def action_new_income(self):
        return self._open_form(self.env._('Nouvel autre revenu'), 'adk.shop.income', {'default_shop_id': self.id})

    def action_new_budget(self):
        return self._open_form(self.env._('Nouveau budget'), 'adk.shop.budget', {'default_shop_id': self.id})

    def action_new_transfer(self):
        return self._open_form(self.env._('Nouveau transfert'), 'adk.shop.transfer', {'default_shop_to_id': self.id})

    def action_new_cash_move(self):
        self.ensure_one()
        return {'type': 'ir.actions.act_window', 'name': self.env._('Mouvement de caisse'),
                'res_model': 'adk.shop.cash.move', 'view_mode': 'form', 'views': [(False, 'form')],
                'target': 'new', 'context': {'default_shop_id': self.id}}

    def action_new_customer(self):
        return self._open_form(self.env._('Nouveau client'), 'res.partner', {
            'default_customer_rank': 1, 'default_adk_shop_ids': [Command.set([self.id])]})

    def action_new_supplier(self):
        return self._open_form(self.env._('Nouveau fournisseur'), 'res.partner', {
            'default_supplier_rank': 1, 'default_adk_shop_ids': [Command.set([self.id])]})

    def action_new_product(self):
        return self._open_form(self.env._('Nouveau produit'), 'product.template', {
            'default_adk_shop_ids': [Command.set([self.id])]})

    def action_open_report(self):
        self.ensure_one()
        return {'type': 'ir.actions.act_window', 'name': self.env._('Rapports'),
                'res_model': 'adk.shop.report.wizard', 'view_mode': 'form', 'views': [(False, 'form')],
                'target': 'new', 'context': {'default_shop_ids': [Command.set([self.id])],
                                             'default_company_id': self.company_id.id}}

    # -- Voir tout -----------------------------------------------------------
    def action_view_sales(self):
        return self._open_list(self.env._('Ventes'), 'sale.order', [('adk_shop_id', '=', self.id)], self._shop_context())

    def action_view_invoices(self):
        return self._open_list(self.env._('Factures clients'), 'account.move',
                               [('adk_shop_id', '=', self.id), ('move_type', 'in', ('out_invoice', 'out_refund'))],
                               self._shop_context(default_move_type='out_invoice'))

    def action_view_payments_in(self):
        return self._open_list(self.env._('Encaissements'), 'account.payment',
                               [('adk_shop_id', '=', self.id), ('payment_type', '=', 'inbound')],
                               {'default_adk_shop_id': self.id, 'default_payment_type': 'inbound'})

    def action_view_incomes(self):
        return self._open_list(self.env._('Autres revenus'), 'adk.shop.income',
                               [('shop_id', '=', self.id)], {'default_shop_id': self.id})

    def action_view_purchases(self):
        return self._open_list(self.env._('Achats'), 'purchase.order', [('adk_shop_id', '=', self.id)], self._shop_context())

    def action_view_bills(self):
        return self._open_list(self.env._('Factures fournisseurs'), 'account.move',
                               [('adk_shop_id', '=', self.id), ('move_type', 'in', ('in_invoice', 'in_refund'))],
                               self._shop_context(default_move_type='in_invoice'))

    def action_view_payments_out(self):
        return self._open_list(self.env._('Paiements fournisseurs'), 'account.payment',
                               [('adk_shop_id', '=', self.id), ('payment_type', '=', 'outbound')],
                               {'default_adk_shop_id': self.id, 'default_payment_type': 'outbound'})

    def action_view_expenses(self):
        return self._open_list(self.env._('Dépenses'), 'adk.shop.expense',
                               [('shop_id', '=', self.id)], {'default_shop_id': self.id})

    def action_view_budgets(self):
        return self._open_list(self.env._('Budgets'), 'adk.shop.budget',
                               [('shop_id', '=', self.id)], {'default_shop_id': self.id})

    def action_view_cash_moves(self):
        return self._open_list(self.env._('Mouvements de caisse'), 'adk.shop.cash.move',
                               [('shop_id', '=', self.id)], {'default_shop_id': self.id})

    def action_view_stock_moves(self):
        return self._open_list(self.env._('Mouvements de stock'), 'stock.move',
                               [('adk_shop_id', '=', self.id)], {}, view_mode='list,pivot,form')

    def action_view_pickings(self):
        return self._open_list(self.env._('Réceptions et livraisons'), 'stock.picking',
                               [('adk_shop_id', '=', self.id)], {})

    def action_view_transfers(self):
        return self._open_list(self.env._('Transferts interboutiques'), 'adk.shop.transfer',
                               ['|', ('shop_from_id', '=', self.id), ('shop_to_id', '=', self.id)],
                               {'default_shop_to_id': self.id})

    def action_view_customers(self):
        return self._open_list(self.env._('Clients'), 'res.partner',
                               [('adk_shop_ids', 'in', self.id), ('customer_rank', '>', 0)],
                               {'default_customer_rank': 1, 'default_adk_shop_ids': [Command.set([self.id])]})

    def action_view_suppliers(self):
        return self._open_list(self.env._('Fournisseurs'), 'res.partner',
                               [('adk_shop_ids', 'in', self.id), ('supplier_rank', '>', 0)],
                               {'default_supplier_rank': 1, 'default_adk_shop_ids': [Command.set([self.id])]})

    def action_view_products(self):
        return self._open_list(self.env._('Produits'), 'product.template',
                               [('adk_shop_ids', 'in', self.id)],
                               {'default_adk_shop_ids': [Command.set([self.id])]}, view_mode='kanban,list,form')

    def action_view_alerts(self):
        return self._open_list(self.env._('Alertes'), 'adk.shop.alert',
                               [('shop_id', '=', self.id), ('state', 'in', ('new', 'ack'))], {})

    def action_view_children(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': self.env._('Boutiques filles'),
            'res_model': 'adk.shop',
            'view_mode': 'kanban,list,form',
            'domain': [('parent_id', '=', self.id)],
            'context': {'default_parent_id': self.id, 'default_company_id': self.company_id.id},
        }
