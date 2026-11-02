# ADK Gestion Boutiques - Odoo 18

Chaque boutique est un espace de travail complet (apercu, revenus, charges, produits et stock, caisse, equipe,
parametres) avec la vente simple du module Ventes d'Odoo (devis, commande, livraison, facture, paiement). Tableau de bord reseau (graphiques, comparaison des boutiques, alertes),
depenses avec approbation, autres revenus, budgets, caisse, transferts entre boutiques, rapports PDF/Excel.

## Installation
1. Supprimer l'ancien dossier `adk_gestion_boutiques` s'il existe, puis copier le nouveau dans vos addons (module autonome : aucun autre module ADK requis).
2. Redemarrer Odoo, mettre a jour la liste des applications, installer ADK Gestion Boutiques.
3. Installer le plan comptable du pays (Facturation > Configuration) et associer les comptes aux categories de depenses / revenus.

## Tests
`odoo-bin -d test -i adk_gestion_boutiques,l10n_cm --without-demo=all --test-enable --test-tags /adk_gestion_boutiques`
(tests automatiques ; les parcours de facturation nécessitent un plan comptable installé).

## Limites
Les transferts inter-societes, la consolidation de gestion en devise et la caisse hors connexion sont operationnels. Les eliminations comptables intragroupe restent a faire en comptabilite. Testez sur une base de recette avant production.
