from odoo import api, fields, models
from odoo.exceptions import ValidationError


class AdkShopGroup(models.Model):
    _name = 'adk.shop.group'
    _description = 'Groupe de boutiques'
    _parent_name = 'parent_id'
    _parent_store = True
    _order = 'complete_name'

    name = fields.Char(string='Nom du groupe', required=True)
    code = fields.Char(string='Code')
    complete_name = fields.Char(compute='_compute_complete_name', store=True, recursive=True)
    parent_id = fields.Many2one('adk.shop.group', string='Groupe parent', index=True, ondelete='restrict')
    parent_path = fields.Char(index=True)
    child_ids = fields.One2many('adk.shop.group', 'parent_id', string='Sous-groupes')
    manager_id = fields.Many2one('res.users', string='Responsable régional', domain="[('share', '=', False)]")
    shop_ids = fields.One2many('adk.shop', 'group_id', string='Boutiques')
    shop_count = fields.Integer(string='Nombre de boutiques', compute='_compute_shop_count')
    color = fields.Integer(string='Couleur')
    active = fields.Boolean(default=True)
    note = fields.Text(string='Notes')

    _sql_constraints = [
        ('name_parent_uniq', 'unique(name, parent_id)', "Ce groupe existe déjà au même niveau."),
    ]

    @api.depends('name', 'parent_id.complete_name')
    def _compute_complete_name(self):
        for group in self:
            if group.parent_id:
                group.complete_name = '%s / %s' % (group.parent_id.complete_name, group.name)
            else:
                group.complete_name = group.name

    @api.depends('shop_ids')
    def _compute_shop_count(self):
        data = self.env['adk.shop']._read_group(
            [('group_id', 'in', self.ids)], ['group_id'], ['__count'])
        mapped = {group.id: count for group, count in data}
        for rec in self:
            rec.shop_count = mapped.get(rec.id, 0)

    @api.constrains('parent_id')
    def _check_parent_recursion(self):
        if self._has_cycle():
            raise ValidationError(self.env._("Un groupe ne peut pas être son propre parent."))

    def action_view_shops(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': self.env._('Boutiques'),
            'res_model': 'adk.shop',
            'view_mode': 'kanban,list,form',
            'domain': [('group_id', 'child_of', self.id)],
            'context': {'default_group_id': self.id},
        }
