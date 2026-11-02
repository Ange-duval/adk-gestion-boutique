import base64
import io

from odoo import api, fields, models
from odoo.exceptions import UserError


class AdkShopReportWizard(models.TransientModel):
    _name = 'adk.shop.report.wizard'
    _description = 'Assistant de rapports boutiques'

    report_type = fields.Selection(
        [('summary', 'Synthèse des boutiques'), ('expenses', 'Dépenses par catégorie'),
         ('stock', 'Stock valorisé par boutique')],
        string='Rapport', required=True, default='summary')
    company_id = fields.Many2one('res.company', string='Société (en-tête)', required=True,
                                 default=lambda self: self.env.company)
    date_from = fields.Date(string='Du', required=True,
                            default=lambda self: fields.Date.context_today(self).replace(day=1))
    date_to = fields.Date(string='Au', required=True, default=fields.Date.context_today)
    group_id = fields.Many2one('adk.shop.group', string='Groupe / région')
    target_currency_id = fields.Many2one(
        'res.currency', string='Consolider en devise',
        help="Laisser vide pour ne jamais mélanger les devises. Sinon, chaque montant est converti au taux "
             "d'Odoo à la date de fin de période, ce qui permet de totaliser des boutiques de sociétés différentes.")
    shop_ids = fields.Many2many('adk.shop', string='Boutiques',
                                help="Laisser vide pour inclure toutes les boutiques autorisées (ou celles du groupe).")
    generated_on = fields.Datetime(default=fields.Datetime.now, readonly=True)

    # ------------------------------------------------------------------------
    def _get_shops(self):
        self.ensure_one()
        if self.shop_ids:
            return self.shop_ids
        domain = []
        if self.group_id:
            domain.append(('group_id', 'child_of', self.group_id.id))
        return self.env['adk.shop'].search(domain)

    def _filters_text(self):
        self.ensure_one()
        shops = self.shop_ids.mapped('code') or (['Groupe : %s' % self.group_id.complete_name] if self.group_id else ['Toutes les boutiques autorisées'])
        return 'Période : %s → %s · Boutiques : %s' % (self.date_from, self.date_to, ', '.join(shops))

    @staticmethod
    def _fmt(value, decimals=2):
        if isinstance(value, (int, float)):
            return ('{:,.%df}' % decimals).format(value).replace(',', '\u202f')
        return value or ''

    def get_report_data(self):
        """Structure générique : titre, colonnes, lignes brutes, lignes formatées, totaux, notes."""
        self.ensure_one()
        shops = self._get_shops()
        method = getattr(self, '_data_%s' % self.report_type)
        columns, rows, notes, row_shops = method(shops)
        if self.target_currency_id and rows:
            rows = self._convert_rows(columns, rows, row_shops)
            notes.append("Montants convertis en %s au taux du %s (consolidation de gestion : sans éliminations intragroupe)."
                         % (self.target_currency_id.name, self.date_to))
        # columns : liste de (libellé, est_numérique, somme_dans_total)
        totals = None
        currencies = {r[2] for r in rows if self.report_type == 'summary'} if self.report_type == 'summary' else set()
        if self.target_currency_id:
            currencies = set()
        if rows and len(currencies) <= 1:
            totals = []
            for i, (label, is_num, summable) in enumerate(columns):
                if i == 0:
                    totals.append('TOTAL')
                elif summable:
                    totals.append(sum(r[i] for r in rows))
                else:
                    totals.append('')
        elif len(currencies) > 1:
            notes.append("Totaux non calculés : plusieurs devises. Choisissez « Consolider en devise » pour les convertir et les totaliser.")
        titles = dict(self._fields['report_type'].selection)
        decimals = self.target_currency_id.decimal_places if self.target_currency_id else max(shops.mapped('currency_id.decimal_places') or [2])
        return {
            'title': titles[self.report_type],
            'columns': [c[0] for c in columns],
            'num_cols': [c[1] for c in columns],
            'rows': rows,
            'display_rows': [[(self._fmt(v, 0 if isinstance(v, int) else decimals), columns[i][1]) for i, v in enumerate(r)] for r in rows],
            'totals': totals,
            'display_totals': [(self._fmt(v, decimals), columns[i][1]) for i, v in enumerate(totals)] if totals else False,
            'notes': notes,
        }

    _CONVERT_COLS = {'summary': (3, 4, 5, 6, 7, 8, 9, 10, 11), 'expenses': (3,), 'stock': (3, 4)}

    def _convert_rows(self, columns, rows, row_shops):
        self.ensure_one()
        target = self.target_currency_id
        out = []
        for row, shop in zip(rows, row_shops):
            row = list(row)
            for i in self._CONVERT_COLS[self.report_type]:
                row[i] = shop.currency_id._convert(row[i], target, shop.company_id, self.date_to)
            if self.report_type == 'summary':
                row[2] = target.name
            out.append(row)
        return out

    # -- Synthèse ------------------------------------------------------------
    def _data_summary(self, shops):
        columns = [
            ('Boutique', False, False), ('Société', False, False), ('Devise', False, False),
            ("CA HT", True, True), ('Marge brute estimée', True, True), ("Charges d'exploitation", True, True),
            ('Dépenses non facturées', True, True), ('Résultat estimé', True, True),
            ('Achats de marchandises', True, True), ('Créances ouvertes', True, True),
            ('Dettes ouvertes', True, True), ('Valeur du stock', True, True)]
        data = shops._get_period_kpis(self.date_from, self.date_to)
        rows = []
        row_shops = []
        for shop in shops:
            d = data.get(shop.id)
            if not d:
                continue
            row_shops.append(shop)
            rows.append([shop.display_name, shop.company_id.name, shop.currency_id.name,
                         d['revenue'], d['margin'], d['charges'], d['expenses_pending'], d['result'],
                         d['goods_purchases'], d['receivable'], d['payable'], d['stock_value']])
        notes = ["Marge brute estimée = CA HT − coût des marchandises vendues (coût actuel des produits).",
                 "Résultat estimé = marge brute + autres revenus non facturés − charges d'exploitation − dépenses approuvées non facturées "
                 "(indicateur de gestion, différent du résultat comptable).",
                 "Les achats de marchandises alimentent le stock : ils ne sont pas comptés comme charges.",
                 "Créances, dettes et stock sont des soldes à la date de génération."]
        return columns, rows, notes, row_shops

    # -- Dépenses ------------------------------------------------------------
    def _data_expenses(self, shops):
        columns = [('Boutique', False, False), ('Catégorie', False, False), ('Nombre', True, True), ('Montant', True, True)]
        grouped = self.env['adk.shop.expense']._read_group(
            [('shop_id', 'in', shops.ids), ('date', '>=', self.date_from), ('date', '<=', self.date_to),
             ('state', 'in', ('approved', 'billed'))],
            ['shop_id', 'category_id'], ['__count', 'amount:sum'])
        pairs = sorted((([shop.display_name, cat.name, count, total], shop) for shop, cat, count, total in grouped),
                       key=lambda p: (p[0][0], p[0][1]))
        rows = [p[0] for p in pairs]
        row_shops = [p[1] for p in pairs]
        currencies = set(shops.mapped('currency_id.name'))
        notes = ["Dépenses approuvées ou facturées sur la période."]
        if len(currencies) > 1:
            notes.append("Plusieurs devises présentes : ne pas additionner les montants entre boutiques de sociétés différentes.")
        return columns, rows, notes, row_shops

    # -- Stock ---------------------------------------------------------------
    def _data_stock(self, shops):
        columns = [('Boutique', False, False), ('Produit', False, False), ('Quantité', True, True),
                   ('Coût unitaire', True, False), ('Valeur', True, True)]
        Quant = self.env['stock.quant'].sudo()
        rows = []
        for shop in shops.filtered('warehouse_id'):
            grouped = Quant._read_group(
                [('location_id', 'child_of', shop.warehouse_id.view_location_id.id),
                 ('location_id.usage', '=', 'internal')], ['product_id'], ['quantity:sum'])
            for product, qty in grouped:
                if not qty:
                    continue
                cost = product.with_company(shop.company_id).standard_price
                rows.append(([shop.display_name, product.display_name, qty, cost, qty * cost], shop))
        rows.sort(key=lambda r: (r[0][0], r[0][1]))
        row_shops = [r[1] for r in rows]
        rows = [r[0] for r in rows]
        return columns, rows, ["Stock à la date de génération, valorisé au coût du produit."], row_shops

    # ------------------------------------------------------------------------
    def action_print_pdf(self):
        self.ensure_one()
        return self.env.ref('adk_gestion_boutiques.action_report_adk_shops').report_action(self)

    def action_export_xlsx(self):
        self.ensure_one()
        try:
            import xlsxwriter
        except ImportError:
            raise UserError(self.env._("La bibliothèque Python xlsxwriter est requise pour l'export Excel."))
        data = self.get_report_data()
        output = io.BytesIO()
        wb = xlsxwriter.Workbook(output, {'in_memory': True})
        ws = wb.add_worksheet(data['title'][:31])
        title = wb.add_format({'bold': True, 'font_size': 14})
        head = wb.add_format({'bold': True, 'bg_color': '#1F3A8A', 'font_color': '#FFFFFF', 'border': 1})
        num = wb.add_format({'num_format': '#,##0.00', 'border': 1})
        txt = wb.add_format({'border': 1})
        tot = wb.add_format({'bold': True, 'num_format': '#,##0.00', 'top': 2})
        ws.write(0, 0, data['title'], title)
        ws.write(1, 0, self.company_id.name)
        ws.write(2, 0, self._filters_text())
        ws.write(3, 0, 'Généré le %s' % fields.Datetime.now())
        for c, label in enumerate(data['columns']):
            ws.write(5, c, label, head)
            ws.set_column(c, c, 22 if not data['num_cols'][c] else 18)
        for r, row in enumerate(data['rows'], start=6):
            for c, value in enumerate(row):
                ws.write(r, c, value, num if data['num_cols'][c] else txt)
        last = 6 + len(data['rows'])
        if data['totals']:
            for c, value in enumerate(data['totals']):
                ws.write(last, c, value, tot)
            last += 1
        for i, note in enumerate(data['notes'], start=last + 1):
            ws.write(i, 0, note)
        wb.close()
        att = self.env['ir.attachment'].create({
            'name': 'ADK_%s_%s.xlsx' % (self.report_type, fields.Date.today()),
            'type': 'binary', 'datas': base64.b64encode(output.getvalue()),
            'res_model': self._name, 'res_id': self.id,
            'mimetype': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'})
        return {'type': 'ir.actions.act_url', 'url': '/web/content/%d?download=true' % att.id, 'target': 'self'}
