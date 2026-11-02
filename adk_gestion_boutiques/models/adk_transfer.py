from odoo import Command, api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError


class AdkShopTransfer(models.Model):
    _name = 'adk.shop.transfer'
    _description = 'Transfert interboutiques'
    _inherit = ['mail.thread', 'mail.activity.mixin', 'adk.open.mixin']
    _order = 'id desc'

    name = fields.Char(string='Référence', default='Nouveau', copy=False, readonly=True)
    shop_from_id = fields.Many2one('adk.shop', string='Boutique expéditrice', required=True, tracking=True,
                                   domain="[('state', '=', 'active'), ('warehouse_id', '!=', False)]")
    shop_to_id = fields.Many2one('adk.shop', string='Boutique destinataire', required=True, tracking=True,
                                 default=lambda self: self.env['adk.shop']._get_default_shop(),
                                 domain="[('state', '=', 'active'), ('warehouse_id', '!=', False)]")
    company_id = fields.Many2one(related='shop_from_id.company_id', store=True, index=True)
    date_request = fields.Date(string='Date de la demande', default=fields.Date.context_today)
    date_expected = fields.Date(string='Livraison souhaitée')
    state = fields.Selection(
        [('draft', 'Brouillon'), ('requested', 'Demandé'), ('approved', 'Validé'),
         ('shipped', 'Expédié'), ('received', 'Reçu'), ('cancelled', 'Annulé')],
        default='draft', required=True, tracking=True, string='État', copy=False)
    line_ids = fields.One2many('adk.shop.transfer.line', 'transfer_id', string='Produits', copy=True)
    note = fields.Text(string='Notes')
    out_picking_id = fields.Many2one('stock.picking', string='Sortie (expédition)', readonly=True, copy=False)
    in_picking_id = fields.Many2one('stock.picking', string='Entrée (réception)', readonly=True, copy=False)

    @api.constrains('shop_from_id', 'shop_to_id')
    def _check_shops(self):
        for rec in self:
            if rec.shop_from_id == rec.shop_to_id:
                raise ValidationError(self.env._("Les boutiques expéditrice et destinataire doivent être différentes."))

    _ADK_GUARDED = {'out_picking_id', 'in_picking_id', 'state'}

    def _adk_check_guarded(self, vals):
        if self.env.context.get('adk_internal') or self.env.su:
            return
        if self._ADK_GUARDED & set(vals) and not self.env.user.has_group('adk_gestion_boutiques.group_adk_director'):
            raise AccessError(self.env._("Ces champs ne peuvent être modifiés que par les boutons du workflow."))

    def write(self, vals):
        self._adk_check_guarded(vals)
        return super().write(vals)

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if not (self.env.context.get('adk_internal') or self.env.su):
                for key in self._ADK_GUARDED:
                    vals.pop(key, None)
            if vals.get('name', 'Nouveau') == 'Nouveau':
                vals['name'] = self.env['ir.sequence'].next_by_code('adk.shop.transfer') or 'Nouveau'
        return super().create(vals_list)

    def unlink(self):
        if any(rec.state not in ('draft', 'cancelled') for rec in self):
            raise UserError(self.env._("Seuls les transferts en brouillon ou annulés peuvent être supprimés."))
        return super().unlink()

    # ------------------------------------------------------------------------
    def _transit_location(self):
        """Emplacement de transit entre deux boutiques (créé automatiquement s'il n'existe pas)."""
        self.ensure_one()
        Location = self.env['stock.location'].sudo()
        if self.shop_from_id.company_id != self.shop_to_id.company_id:
            # entre sociétés : emplacement de transit commun, sans société (il sert d'intermédiaire aux deux entrepôts)
            loc = Location.search([('usage', '=', 'transit'), ('company_id', '=', False)], limit=1)
            if not loc:
                loc = Location.create({'name': self.env._('Transit intersociétés'), 'usage': 'transit', 'company_id': False})
            return loc
        loc = Location.search([('usage', '=', 'transit'), ('company_id', 'in', [False, self.company_id.id])], limit=1)
        if not loc:
            loc = Location.create({'name': self.env._('Transit interboutiques'), 'usage': 'transit',
                                   'company_id': self.company_id.id})
        return loc

    def _create_picking(self, picking_type, src, dst, qty_field):
        self.ensure_one()
        company = picking_type.company_id or self.company_id
        moves = []
        for line in self.line_ids:
            qty = line[qty_field]
            if qty <= 0:
                continue
            moves.append(Command.create({
                'name': line.product_id.display_name,
                'product_id': line.product_id.id,
                'product_uom_qty': qty,
                'product_uom': line.uom_id.id,
                'location_id': src.id,
                'location_dest_id': dst.id,
                'company_id': company.id,
            }))
        if not moves:
            raise UserError(self.env._("Aucune quantité à transférer."))
        picking = self.env['stock.picking'].sudo().with_company(company).create({
            'picking_type_id': picking_type.id,
            'location_id': src.id,
            'location_dest_id': dst.id,
            'origin': self.name,
            'company_id': company.id,
            'move_ids': moves,
        })
        picking.action_confirm()
        picking.action_assign()
        return picking

    def action_request(self):
        self = self.with_context(adk_internal=True)
        for rec in self.filtered(lambda r: r.state == 'draft'):
            if not rec.line_ids:
                raise UserError(self.env._("Ajoutez au moins un produit."))
            rec.state = 'requested'
            manager = rec.shop_from_id.manager_id
            if manager:
                rec.activity_schedule('mail.mail_activity_data_todo', user_id=manager.id,
                                      summary=self.env._("Demande de transfert à valider : %s", rec.name))

    def action_approve(self):
        self = self.with_context(adk_internal=True)
        for rec in self.filtered(lambda r: r.state == 'requested'):
            user = self.env.user
            allowed = user.has_group('adk_gestion_boutiques.group_adk_director') or (
                user.has_group('adk_gestion_boutiques.group_adk_manager')
                and user in (rec.shop_from_id.manager_id | rec.shop_from_id.group_id.manager_id))
            if not allowed:
                raise UserError(self.env._("Seul le responsable de la boutique expéditrice peut valider ce transfert."))
            for line in rec.line_ids:
                line.qty_sent = line.qty_requested
            rec.state = 'approved'
            rec.activity_feedback(['mail.mail_activity_data_todo'])

    def action_ship(self):
        self = self.with_context(adk_internal=True)
        for rec in self.filtered(lambda r: r.state == 'approved'):
            src_wh = rec.shop_from_id.warehouse_id
            picking = rec._create_picking(src_wh.int_type_id, src_wh.lot_stock_id, rec._transit_location(), 'qty_sent')
            rec.write({'out_picking_id': picking.id, 'state': 'shipped'})
            rec.message_post(body=self.env._("Sortie de stock %s créée : validez-la pour décrémenter le stock.", picking.name))

    def action_receive(self):
        self = self.with_context(adk_internal=True)
        for rec in self.filtered(lambda r: r.state == 'shipped'):
            if rec.out_picking_id.state != 'done':
                raise UserError(self.env._("Validez d'abord la sortie de stock %s de la boutique expéditrice.", rec.out_picking_id.name))
            dst_wh = rec.shop_to_id.warehouse_id
            picking = rec._create_picking(dst_wh.int_type_id, rec._transit_location(), dst_wh.lot_stock_id, 'qty_sent')
            rec.write({'in_picking_id': picking.id, 'state': 'received'})
            rec.message_post(body=self.env._("Entrée de stock %s créée : validez-la pour confirmer la réception.", picking.name))

    def action_cancel(self):
        self = self.with_context(adk_internal=True)
        for rec in self:
            if rec.state in ('shipped', 'received'):
                raise UserError(self.env._("Un transfert expédié ne peut plus être annulé : annulez ou retournez les mouvements de stock."))
            rec.state = 'cancelled'

    def action_draft(self):
        self = self.with_context(adk_internal=True)
        self.filtered(lambda r: r.state == 'cancelled').write({'state': 'draft'})


class AdkShopTransferLine(models.Model):
    _name = 'adk.shop.transfer.line'
    _description = 'Ligne de transfert interboutiques'

    transfer_id = fields.Many2one('adk.shop.transfer', required=True, ondelete='cascade', index=True)
    product_id = fields.Many2one('product.product', string='Produit', required=True,
                                 domain="[('is_storable', '=', True)]")
    uom_id = fields.Many2one(related='product_id.uom_id', string='Unité')
    qty_requested = fields.Float(string='Quantité demandée', required=True, default=1.0)
    qty_sent = fields.Float(string='Quantité expédiée')
    available_qty = fields.Float(string='Disponible (expéditeur)', compute='_compute_available',
                                 help="Quantité en stock dans l'entrepôt de la boutique expéditrice.")

    _sql_constraints = [('qty_positive', 'check(qty_requested > 0)', "La quantité demandée doit être positive.")]

    @api.depends('product_id', 'transfer_id.shop_from_id')
    def _compute_available(self):
        for line in self:
            wh = line.transfer_id.shop_from_id.warehouse_id
            line.available_qty = line.product_id.with_context(warehouse_id=wh.id).qty_available if wh and line.product_id else 0.0
