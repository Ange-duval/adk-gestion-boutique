from odoo import models


class AdkOpenMixin(models.AbstractModel):
    _name = 'adk.open.mixin'
    _description = "Ouverture rapide d'un document depuis l'espace boutique"

    def action_adk_open_form(self):
        """Bouton « ouvrir » des listes de l'espace boutique : ouvre le document en pleine page."""
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'res_model': self._name,
            'res_id': self.id,
            'view_mode': 'form',
            'views': [(False, 'form')],
            'target': 'current',
        }
