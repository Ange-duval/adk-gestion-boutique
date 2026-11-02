from odoo import fields
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tests import Form, TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestAdkShop(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Shop = cls.env['adk.shop']
        cls.shop = cls.Shop.create({'name': 'BEN Douala', 'code': 'ben-dla', 'state': 'active'})
        cls.category = cls.env['adk.expense.category'].create({'name': 'Catégorie test ADK'})

    def _expense(self, **kw):
        vals = {'shop_id': self.shop.id, 'category_id': self.category.id,
                'description': 'Test', 'amount': 100.0}
        vals.update(kw)
        return self.env['adk.shop.expense'].create(vals)

    # --- boutique / intégration native
    def test_shop_creation_creates_tag(self):
        self.assertEqual(self.shop.code, 'BEN-DLA')
        self.assertTrue(self.shop.tag_id)
        self.shop.name = 'BEN Yaoundé'
        self.assertIn('BEN Yaoundé', self.shop.tag_id.name)

    def test_partner_gets_shop_tag(self):
        partner = self.env['res.partner'].create({
            'name': 'Client Test', 'customer_rank': 1, 'adk_shop_ids': [(6, 0, self.shop.ids)]})
        self.assertIn(self.shop.tag_id, partner.category_id)
        self.assertIn(partner, self.shop.customer_ids)
        partner.adk_shop_ids = [(5, 0, 0)]
        self.assertNotIn(self.shop.tag_id, partner.category_id)

    def test_unique_code_per_company(self):
        with self.assertRaises(Exception):
            self.Shop.create({'name': 'Autre', 'code': 'ben-dla'})

    def test_no_parent_cycle(self):
        child = self.Shop.create({'name': 'B', 'code': 'B', 'parent_id': self.shop.id})
        with self.assertRaises(UserError):
            self.shop.parent_id = child

    def test_kpis_without_documents(self):
        for period in ('today', 'week', 'month', 'last_month', 'quarter', 'year'):
            self.shop.kpi_period = period
            self.assertEqual(self.shop.kpi_revenue, 0.0)
            self.assertEqual(self.shop.kpi_result, 0.0)
            self.assertTrue(self.shop.kpi_label)

    def test_dashboard_data(self):
        for period in ('today', 'week', 'month', 'last_month', 'quarter', 'year'):
            data = self.Shop.get_dashboard_data(period)
            self.assertEqual(data['period'], period)
            self.assertIn(self.shop.id, [row['id'] for row in data['shops']])
            self.assertTrue(data['series'])
            self.assertIn('revenue', data['totals'])

    def test_first_cash_count_is_not_a_gap(self):
        Cash = self.env['adk.shop.cash.move']
        opening = Cash.create({'shop_id': self.shop.id, 'kind': 'count', 'amount': 5000, 'reason': 'Fond de caisse'})
        self.assertEqual(opening.difference, 0.0)
        self.assertFalse(self.env['adk.shop.alert'].search([('shop_id', '=', self.shop.id), ('alert_type', '=', 'cash')]))

    # --- espace boutique : vues et actions
    def test_workspace_form_loads(self):
        form = Form(self.shop)
        self.assertEqual(form.name, 'BEN Douala')

    def test_all_module_actions_open(self):
        """Chaque action du module doit pouvoir construire ses vues (liste, formulaire, kanban, graphique…)."""
        data = self.env['ir.model.data'].search([('module', '=', 'adk_gestion_boutiques'),
                                                 ('model', '=', 'ir.actions.act_window')])
        self.assertTrue(data)
        for rec in data:
            action = self.env['ir.actions.act_window'].browse(rec.res_id)
            model = self.env[action.res_model]
            views = [(False, mode) for mode in action.view_mode.split(',')]
            model.get_views(views)

    def test_shop_navigation_actions(self):
        names = [n for n in dir(self.Shop) if n.startswith('action_new_') or n.startswith('action_view_')]
        self.assertGreater(len(names), 20)
        for name in names:
            act = getattr(self.shop, name)()
            self.assertEqual(act['type'], 'ir.actions.act_window', name)
            views = [(False, m) for m in act['view_mode'].split(',')]
            self.env[act['res_model']].get_views(views)
        self.shop.sale_mode = 'simple'
        with self.assertRaises(UserError):
            self.shop.action_open_pos()

    # --- dépenses
    def test_expense_auto_approval(self):
        self.shop.expense_auto_approve_limit = 200
        exp = self._expense(amount=150)
        exp.action_submit()
        self.assertEqual(exp.state, 'approved')
        big = self._expense(amount=500)
        big.action_submit()
        self.assertEqual(big.state, 'submitted')

    def test_expense_bill_requires_partner_and_account(self):
        self.shop.expense_auto_approve_limit = 200
        exp = self._expense()
        exp.action_submit()
        with self.assertRaises(UserError):
            exp.action_create_bill()
        exp.partner_id = self.env['res.partner'].create({'name': 'Fournisseur test'})
        has_account = bool(exp.category_id._default_account(exp.company_id))
        if has_account:
            exp.action_create_bill()                    # compte natif par défaut : aucune configuration préalable
            self.assertTrue(exp.move_id)
        else:
            with self.assertRaises(UserError):
                exp.action_create_bill()

    def test_workflow_fields_protected(self):
        user = self.env['res.users'].create({
            'name': 'Vendeur', 'login': 'vendeur_adk',
            'groups_id': [(6, 0, [self.env.ref('adk_gestion_boutiques.group_adk_user').id])]})
        self.shop.user_ids = [(4, user.id)]
        exp = self._expense().with_user(user)
        with self.assertRaises(AccessError):
            exp.write({'state': 'approved'})

    # --- budgets
    def test_budget_spent(self):
        exp = self._expense(amount=300)
        exp.with_context(adk_internal=True).write({'state': 'approved'})
        today = fields.Date.today()
        budget = self.env['adk.shop.budget'].create({
            'name': 'Budget test', 'shop_id': self.shop.id, 'date_from': today, 'date_to': today,
            'line_ids': [(0, 0, {'category_id': self.category.id, 'planned_amount': 1000})]})
        self.assertEqual(budget.line_ids.spent_amount, 300)
        self.assertEqual(budget.percent, 30)
        budget.action_confirm()
        self.env['adk.shop.alert']._cron_generate_alerts()

    # --- autres revenus
    def test_income_workflow(self):
        cat = self.env['adk.income.category'].create({'name': 'Revenu test ADK'})
        income = self.env['adk.shop.income'].create({
            'shop_id': self.shop.id, 'category_id': cat.id, 'description': 'Location', 'amount': 500})
        self.assertEqual(income.state, 'draft')
        plain = self._plain_user()
        forged = self.env['adk.shop.income'].with_user(plain).create({
            'shop_id': self.shop.id, 'category_id': cat.id, 'description': 'x', 'amount': 1, 'state': 'confirmed'})
        self.assertEqual(forged.state, 'draft')          # l'état forcé à la création est ignoré
        with self.assertRaises(AccessError):
            forged.write({'state': 'invoiced'})          # et ne peut pas être écrit directement
        with self.assertRaises(UserError):
            forged.action_confirm()                      # pas de confirmation par un simple utilisateur
        income.action_confirm()   # administrateur = direction
        self.assertEqual(income.state, 'confirmed')
        self.assertEqual(self.shop.kpi_income_pending, 500.0)
        with self.assertRaises(UserError):
            income.action_create_invoice()   # pas de client

    def _plain_user(self):
        user = self.env['res.users'].create({
            'name': 'Simple', 'login': 'simple_adk',
            'groups_id': [(6, 0, [self.env.ref('adk_gestion_boutiques.group_adk_user').id])]})
        self.shop.user_ids = [(4, user.id)]
        return user

    # --- caisse
    def test_cash_register(self):
        Cash = self.env['adk.shop.cash.move']
        Cash.create({'shop_id': self.shop.id, 'kind': 'count', 'amount': 10000, 'reason': 'Fond de caisse'})
        Cash.create({'shop_id': self.shop.id, 'kind': 'in', 'amount': 5000, 'reason': 'Apport'})
        Cash.create({'shop_id': self.shop.id, 'kind': 'out', 'amount': 2000, 'reason': 'Fournitures'})
        self.assertEqual(self.shop.cash_balance, 13000.0)
        with self.assertRaises(UserError):
            Cash.create({'shop_id': self.shop.id, 'kind': 'out', 'amount': 99999, 'reason': 'Trop'})
        gap = Cash.create({'shop_id': self.shop.id, 'kind': 'count', 'amount': 12500, 'reason': 'Clôture'})
        self.assertEqual(gap.expected, 13000.0)
        self.assertEqual(gap.difference, -500.0)
        self.assertEqual(self.shop.cash_balance, 12500.0)
        self.assertTrue(self.env['adk.shop.alert'].search([('shop_id', '=', self.shop.id), ('alert_type', '=', 'cash')]))
        with self.assertRaises(UserError):
            gap.with_user(self.env.ref('base.user_admin')).write({'amount': 1})

    # --- rapports
    def test_report_data(self):
        for rtype in ('summary', 'expenses', 'stock'):
            wiz = self.env['adk.shop.report.wizard'].create({'report_type': rtype, 'shop_ids': [(6, 0, self.shop.ids)]})
            data = wiz.get_report_data()
            self.assertTrue(data['columns'])
        wiz.action_print_pdf()
