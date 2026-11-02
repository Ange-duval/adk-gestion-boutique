# -*- coding: utf-8 -*-
"""Création des catégories usuelles (revenus et dépenses), sans jamais entrer en conflit
avec des catégories déjà présentes dans la base (contrainte d'unicité sur le nom)."""

INCOME_CATEGORIES = [
    ("Commissions", 10),
    ("Location d'espace", 20),
    ("Prestations de services", 30),
    ("Subventions et aides", 40),
    ("Revenus divers", 50),
]

EXPENSE_CATEGORIES = [
    ("Loyer", 10),
    ("Électricité", 20),
    ("Eau", 30),
    ("Télécommunications", 40),
    ("Transport", 50),
    ("Entretien et réparations", 60),
    ("Fournitures", 70),
    ("Personnel et frais", 80),
    ("Marketing et publicité", 90),
    ("Autres charges", 100),
]


def _seed(env, model_name, items):
    Model = env[model_name].with_context(active_test=False, lang='en_US')
    existing = set(Model.search([]).mapped('name'))
    to_create = [{'name': name, 'sequence': seq} for name, seq in items if name not in existing]
    if to_create:
        Model.create(to_create)


def seed_categories(env):
    _seed(env, 'adk.income.category', INCOME_CATEGORIES)
    _seed(env, 'adk.expense.category', EXPENSE_CATEGORIES)


def seed_accounts(env):
    """Renseigne les comptes natifs par défaut sur les catégories (sans écraser ce que l'utilisateur a choisi)."""
    for model in ('adk.income.category', 'adk.expense.category'):
        for company in env['res.company'].search([]):
            for cat in env[model].with_context(active_test=False).with_company(company).search([]):
                if not cat.account_id:
                    acc = cat._default_account(company)
                    if acc:
                        cat.account_id = acc


def post_init_hook(env):
    seed_categories(env)
    seed_accounts(env)
