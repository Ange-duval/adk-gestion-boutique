/** @odoo-module **/

import { Component, onWillStart, useState } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { formatMonetary } from "@web/views/fields/formatters";

const PERIODS = [
    ["today", "Aujourd'hui"],
    ["week", "7 jours"],
    ["month", "Ce mois"],
    ["last_month", "Mois précédent"],
    ["quarter", "Trimestre"],
    ["year", "Année"],
];

export class AdkDashboard extends Component {
    static template = "adk_gestion_boutiques.Dashboard";
    static props = ["*"];

    setup() {
        this.orm = useService("orm");
        this.action = useService("action");
        this.periods = PERIODS;
        this.state = useState({ period: "month", data: null, loading: false });
        onWillStart(() => this.load());
    }

    async load() {
        this.state.loading = true;
        try {
            this.state.data = await this.orm.call("adk.shop", "get_dashboard_data", [], { period: this.state.period });
        } finally {
            this.state.loading = false;
        }
    }

    async setPeriod(period) {
        this.state.period = period;
        await this.load();
    }

    // ---------- formats ----------
    money(value) {
        const data = this.state.data;
        if (!data || !data.currency_id) {
            return "—";
        }
        return formatMonetary(value || 0, { currencyId: data.currency_id, digits: [16, 0] });
    }

    shopMoney(shop, value) {
        return formatMonetary(value || 0, { currencyId: shop.currency_id, digits: [16, 0] });
    }

    compact(value) {
        const v = Math.abs(value);
        const sign = value < 0 ? "-" : "";
        if (v >= 1e6) {
            return sign + (v / 1e6).toFixed(v >= 1e7 ? 0 : 1).replace(".", ",") + " M";
        }
        if (v >= 1e3) {
            return sign + Math.round(v / 1e3) + " k";
        }
        return sign + Math.round(v);
    }

    fmtDate(str) {
        return new Date(str + "T00:00:00").toLocaleDateString("fr-FR", { day: "numeric", month: "long", year: "numeric" });
    }

    // ---------- indicateurs ----------
    get tiles() {
        const d = this.state.data;
        if (!d) {
            return [];
        }
        const t = d.totals;
        const m = (k) => (d.single_currency ? this.money(t[k]) : "—");
        return [
            { label: "Chiffre d'affaires HT", value: m("revenue"), color: "rev", icon: "fa-line-chart", delta: t.revenue_delta },
            { label: "Marge brute estimée", value: m("margin"), color: "rev", icon: "fa-percent" },
            { label: "Charges d'exploitation", value: m("charges"), color: "chg", icon: "fa-shopping-basket" },
            { label: "Résultat estimé", value: m("result"), color: "res", icon: "fa-balance-scale" },
            { label: "Encaissé", value: m("collected"), color: "rev", icon: "fa-money" },
            { label: "Créances clients", value: m("receivable"), color: "wrn", icon: "fa-hourglass-half", sub: t.overdue_count ? t.overdue_count + " en retard" : "" },
            { label: "Dettes fournisseurs", value: m("payable"), color: "chg", icon: "fa-truck" },
            { label: "Valeur du stock", value: m("stock_value"), color: "res", icon: "fa-cubes" },
        ];
    }

    // ---------- graphique du chiffre d'affaires ----------
    get chart() {
        const pts = (this.state.data && this.state.data.series) || [];
        const W = 640, H = 180, PL = 44, PB = 22, PT = 10;
        const max = Math.max(...pts.map((p) => p.value), 1);
        const n = pts.length;
        const x = (i) => PL + (n > 1 ? (i * (W - PL - 8)) / (n - 1) : (W - PL) / 2);
        const y = (v) => PT + (H - PT - PB) * (1 - v / max);
        const line = pts.map((p, i) => `${i ? "L" : "M"}${x(i).toFixed(1)},${y(p.value).toFixed(1)}`).join(" ");
        const area = n ? `${line} L${x(n - 1).toFixed(1)},${H - PB} L${x(0).toFixed(1)},${H - PB} Z` : "";
        const every = Math.max(1, Math.ceil(n / 8));
        return {
            W, H, line, area,
            grid: [0, 0.5, 1].map((r) => ({ y: y(max * r), label: this.compact(max * r) })),
            labels: pts.map((p, i) => ({ x: x(i), text: p.label, show: i % every === 0 })),
            dots: pts.map((p, i) => ({ x: x(i), y: y(p.value), title: p.label + " : " + this.money(p.value) })),
        };
    }

    shopBar(shop, key) {
        const max = Math.max(...this.state.data.shops.map((s) => Math.max(s.revenue, s.charges + s.goods_purchases, 1)), 1);
        const v = key === "charges" ? shop.charges + shop.goods_purchases : shop[key];
        return Math.max(0, Math.min(100, (v / max) * 100));
    }

    barPct(value, list) {
        const max = Math.max(...list.map((i) => i.amount), 1);
        return Math.max(2, (value / max) * 100);
    }

    initials(name) {
        return name.replace(/[^\p{L}\p{N} ]/gu, "").split(" ").filter(Boolean).slice(0, 2).map((w) => w[0]).join("").toUpperCase();
    }

    // ---------- navigation ----------
    openShop(id) {
        this.action.doAction({
            type: "ir.actions.act_window", res_model: "adk.shop", res_id: id, views: [[false, "form"]], target: "current",
        });
    }

    async shopAction(id, method) {
        const act = await this.orm.call("adk.shop", method, [[id]]);
        if (act) {
            await this.action.doAction(act);
        }
    }

    openAlerts() {
        this.action.doAction("adk_gestion_boutiques.action_adk_alert");
    }

    openReports() {
        this.action.doAction("adk_gestion_boutiques.action_adk_report_wizard");
    }
}

registry.category("actions").add("adk_dashboard", AdkDashboard);
