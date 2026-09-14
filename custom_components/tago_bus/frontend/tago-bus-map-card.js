/*
 * TAGO Bus Map Card
 * Route lines, stops and live buses from the tago_bus integration.
 * Hover (or tap) for details, tap a stop to make it the route's favorite,
 * and a collapsible overview shows arrivals at each favorite stop.
 *
 * type: custom:tago-bus-map-card
 * entry_id: optional, limit to one configured route
 * height: 400
 * show_stops: true
 * show_route_line: true
 * show_legend: true
 * show_overview: true
 * dark_mode: auto | light | dark
 */

const CARD_VERSION = "0.3.0";
const LEAFLET_BASE = "https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.9.4";
const TILE_URL = "https://tile.openstreetmap.org/{z}/{x}/{y}.png";
const TILE_ATTRIBUTION =
  '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors';
const FALLBACK_COLORS = ["#1e88e5", "#f4511e", "#43a047", "#8e24aa"];
const ROUTE_REFRESH_MS = 10 * 60 * 1000;
const GEOMETRY_RETRY_MS = 15 * 1000;
const OVERVIEW_STORAGE_KEY = "tago-bus-map-card:overview-open";

let leafletPromise;
function loadLeaflet() {
  if (window.L && window.L.map) return Promise.resolve(window.L);
  if (!leafletPromise) {
    leafletPromise = new Promise((resolve, reject) => {
      const script = document.createElement("script");
      script.src = `${LEAFLET_BASE}/leaflet.min.js`;
      script.onload = () => resolve(window.L);
      script.onerror = () => {
        leafletPromise = undefined;
        reject(new Error("Leaflet을 불러오지 못했습니다 (인터넷 연결 확인)"));
      };
      document.head.appendChild(script);
    });
  }
  return leafletPromise;
}

const esc = (value) =>
  String(value ?? "").replace(
    /[&<>"']/g,
    (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c],
  );

function formatAgo(isoString) {
  const seconds = Math.max(0, Math.round((Date.now() - new Date(isoString).getTime()) / 1000));
  if (seconds < 60) return `${seconds}초 전`;
  return `${Math.floor(seconds / 60)}분 전`;
}

function formatMinutes(minutes) {
  if (minutes === null || minutes === undefined) return null;
  const m = Number(minutes);
  if (Number.isNaN(m)) return null;
  return m <= 0 ? "곧 도착" : `${m}분`;
}

function formatStops(stops) {
  if (stops === null || stops === undefined) return null;
  return stops === 0 ? "도착" : `${stops}정거장 전`;
}

function readOverviewOpen() {
  try {
    const value = localStorage.getItem(OVERVIEW_STORAGE_KEY);
    return value === null ? true : value === "1";
  } catch {
    return true;
  }
}

function writeOverviewOpen(open) {
  try {
    localStorage.setItem(OVERVIEW_STORAGE_KEY, open ? "1" : "0");
  } catch {
    // Storage can be unavailable (private mode); the toggle still works.
  }
}

class TagoBusMapCard extends HTMLElement {
  static getStubConfig() {
    return {};
  }

  setConfig(config) {
    this._config = {
      height: 400,
      show_stops: true,
      show_route_line: true,
      show_legend: true,
      show_overview: true,
      dark_mode: "auto",
      scroll_wheel_zoom: true,
      ...config,
    };
    if (this._container) {
      this._container.style.height = `${Number(this._config.height) || 400}px`;
      this._drawStatic();
      this._renderOverview();
    }
  }

  set hass(hass) {
    this._hass = hass;
    if (!this._started) {
      this._started = true;
      this._init();
      return;
    }
    if (this._map) {
      this._syncTheme();
      this._updateBuses();
      this._refreshOpenOverlays();
      this._renderOverview();
    }
  }

  getCardSize() {
    return Math.ceil((Number(this._config?.height) || 400) / 50);
  }

  connectedCallback() {
    if (this._map) setTimeout(() => this._map.invalidateSize(), 0);
    if (!this._refreshTimer && this._started) this._startRefreshTimer();
  }

  disconnectedCallback() {
    clearInterval(this._refreshTimer);
    this._refreshTimer = undefined;
  }

  // ---------------------------------------------------------------- setup

  async _init() {
    const root = this.attachShadow({ mode: "open" });
    root.innerHTML = `
      <link rel="stylesheet" href="${LEAFLET_BASE}/leaflet.min.css">
      <style>
        ha-card { overflow: hidden; position: relative; }
        .map { width: 100%; }
        .message { position: absolute; inset: 0; display: flex; align-items: center;
          justify-content: center; padding: 16px; text-align: center; color: var(--secondary-text-color);
          pointer-events: none; z-index: 1000; }
        .message[hidden] { display: none; }
        .dark .leaflet-tile-pane { filter: invert(1) hue-rotate(180deg) brightness(0.9) contrast(0.9); }
        .bus-icon, .fav-icon { background: none; border: none; }
        .bus { width: 30px; height: 30px; border-radius: 50%; color: #fff; font: 700 13px/30px Arial, sans-serif;
          text-align: center; border: 2px solid #fff; box-sizing: border-box;
          box-shadow: 0 1px 4px rgba(0,0,0,.45); }
        .fav { width: 22px; height: 22px; border-radius: 50%; color: #ffd54f; font: 15px/19px Arial, sans-serif;
          text-align: center; border: 2px solid #fff; box-sizing: border-box; box-shadow: 0 1px 4px rgba(0,0,0,.45); }
        .panel { font: 13px/1.45 var(--paper-font-body1_-_font-family, sans-serif);
          background: var(--card-background-color, #fff); color: var(--primary-text-color, #222);
          border: 1px solid var(--divider-color, #ddd); border-radius: 10px; box-shadow: 0 2px 8px rgba(0,0,0,.25); }
        .leaflet-tooltip.tip, .leaflet-popup.pop .leaflet-popup-content-wrapper {
          font: 13px/1.45 var(--paper-font-body1_-_font-family, sans-serif);
          background: var(--card-background-color, #fff); color: var(--primary-text-color, #222);
          border-radius: 8px; box-shadow: 0 2px 8px rgba(0,0,0,.25); }
        .leaflet-tooltip.tip { padding: 6px 10px; width: max-content; max-width: 260px; white-space: normal;
          border: 1px solid var(--divider-color, #ddd); }
        .leaflet-tooltip.tip::before { display: none; }
        .leaflet-popup.pop .leaflet-popup-content { margin: 10px 12px; min-width: 180px; }
        .leaflet-popup.pop .leaflet-popup-tip { background: var(--card-background-color, #fff); }
        .leaflet-popup.pop a.leaflet-popup-close-button { color: var(--secondary-text-color, #666); }
        b.title { font-size: 14px; }
        .muted { color: var(--secondary-text-color, #666); font-size: 12px; }
        .row { margin-top: 2px; }
        .hl { font-weight: 700; color: var(--primary-color, #03a9f4); }
        .fav-btn { margin-top: 8px; width: 100%; padding: 6px 8px; border-radius: 6px; cursor: pointer;
          font: 600 13px var(--paper-font-body1_-_font-family, sans-serif);
          border: 1px solid var(--primary-color, #03a9f4); background: var(--primary-color, #03a9f4); color: #fff; }
        .fav-btn.on { background: transparent; color: var(--primary-color, #03a9f4); }
        .fav-btn:disabled { opacity: .6; cursor: progress; }
        .legend { padding: 6px 10px; font-size: 12px; line-height: 1.6; }
        .legend i { display: inline-block; width: 10px; height: 10px; border-radius: 50%;
          margin-right: 6px; vertical-align: middle; }
        .overview { width: 250px; max-width: calc(100vw - 80px); overflow: hidden; }
        .overview.closed { width: auto; }
        .ov-head { display: flex; align-items: center; gap: 6px; padding: 7px 10px; cursor: pointer;
          user-select: none; font-weight: 700; background: none; border: none; width: 100%;
          color: inherit; font: inherit; font-weight: 700; text-align: left; }
        .ov-head .chev { margin-left: auto; transition: transform .2s; }
        .overview.closed .chev { transform: rotate(180deg); }
        .ov-body { max-height: 260px; overflow-y: auto; border-top: 1px solid var(--divider-color, #ddd); }
        .overview.closed .ov-body { display: none; }
        .ov-route { padding: 8px 10px; cursor: pointer; border-left: 4px solid transparent; }
        .ov-route + .ov-route { border-top: 1px solid var(--divider-color, #eee); }
        .ov-route:hover { background: rgba(127,127,127,.08); }
        .ov-route .name { font-weight: 600; }
        .ov-bus { display: flex; justify-content: space-between; gap: 8px; margin-top: 3px; }
        .ov-bus .min { font-weight: 700; color: var(--primary-color, #03a9f4); }
      </style>
      <ha-card>
        <div class="map"></div>
        <div class="message">지도를 불러오는 중…</div>
      </ha-card>`;
    this._container = root.querySelector(".map");
    this._message = root.querySelector(".message");
    this._container.style.height = `${Number(this._config.height) || 400}px`;

    // Leaflet measures the container on creation, so wait for its CSS too.
    const css = root.querySelector("link");
    const cssLoaded = new Promise((resolve) => {
      css.addEventListener("load", resolve, { once: true });
      css.addEventListener("error", resolve, { once: true });
    });

    let L;
    try {
      [L] = await Promise.all([loadLeaflet(), cssLoaded]);
    } catch (err) {
      this._showMessage(err.message);
      return;
    }
    this._L = L;
    this._map = L.map(this._container, {
      zoomControl: true,
      // Fade relies on animation frames, which are paused in background tabs
      // and left tiles invisible there.
      fadeAnimation: false,
      scrollWheelZoom: this._config.scroll_wheel_zoom,
    });
    L.tileLayer(TILE_URL, { attribution: TILE_ATTRIBUTION, maxZoom: 19 }).addTo(this._map);
    this._syncTheme();
    this._map.setView([this._hass.config.latitude, this._hass.config.longitude], 13);

    // Route lines are drawn as SVG vectors below stops and buses.
    this._map.createPane("routeLines").style.zIndex = 390;
    this._lineRenderer = L.svg({ pane: "routeLines" });
    this._lineLayer = L.layerGroup().addTo(this._map);
    this._stopLayer = L.layerGroup().addTo(this._map);
    this._busLayer = L.layerGroup().addTo(this._map);
    this._busMarkers = new Map();
    this._stopMarkers = [];
    this._overviewOpen = readOverviewOpen();

    new ResizeObserver(() => this._map.invalidateSize()).observe(this._container);

    await this._loadRoutes(true);
    this._startRefreshTimer();
  }

  _startRefreshTimer() {
    this._refreshTimer = setInterval(() => this._loadRoutes(false), ROUTE_REFRESH_MS);
  }

  async _loadRoutes(fit) {
    try {
      const msg = { type: "tago_bus/routes" };
      if (this._config.entry_id) msg.entry_id = this._config.entry_id;
      this._setEntries(await this._hass.callWS(msg));
    } catch (err) {
      this._showMessage(`노선 정보를 불러오지 못했습니다: ${err.message || err.code || err}`);
      return;
    }
    if (!this._entries.length) {
      this._showMessage("설정된 TAGO 버스 노선이 없습니다.");
      setTimeout(() => this._loadRoutes(true), 30000);
      return;
    }
    this._showMessage(null);
    if (fit) this._fitBounds();
    // Road geometry is computed in the background on first setup.
    if (this._entries.some((e) => !e.geometry_ready)) {
      clearTimeout(this._geometryTimer);
      this._geometryTimer = setTimeout(() => this._loadRoutes(false), GEOMETRY_RETRY_MS);
    }
  }

  _setEntries(entries) {
    this._entries = entries;
    this._routes = new Map();
    this._directions = new Map();
    for (const entry of entries) {
      for (const route of entry.routes) {
        this._routes.set(route.route_id, { ...route, entry_id: entry.entry_id });
        for (const d of route.directions) this._directions.set(d.index, d);
      }
    }
    this._drawStatic();
    this._updateBuses();
    this._renderOverview(true);
  }

  _color(index) {
    return this._directions?.get(index)?.color || FALLBACK_COLORS[index % FALLBACK_COLORS.length];
  }

  // ---------------------------------------------------------------- drawing

  _drawStatic() {
    if (!this._map || !this._routes) return;
    const L = this._L;
    this._lineLayer.clearLayers();
    this._stopLayer.clearLayers();
    this._stopMarkers = [];
    if (this._legend) this._legend.remove();

    for (const route of this._routes.values()) {
      if (this._config.show_route_line) {
        for (const line of route.lines) {
          L.polyline(line.coordinates, {
            renderer: this._lineRenderer,
            color: this._color(line.direction_index),
            weight: 6,
            opacity: 0.45,
            lineCap: "round",
            lineJoin: "round",
            interactive: false,
          }).addTo(this._lineLayer);
        }
      }

      for (const station of route.stations) {
        const favorite = route.favorite_order === station.order;
        if (!this._config.show_stops && !favorite) continue;
        const color = this._color(station.direction_index);
        const latlng = [station.latitude, station.longitude];
        const marker = favorite
          ? L.marker(latlng, {
              icon: L.divIcon({
                className: "fav-icon",
                html: `<div class="fav" style="background:${esc(color)}">★</div>`,
                iconSize: [22, 22],
                iconAnchor: [11, 11],
              }),
              zIndexOffset: 500,
            })
          : L.circleMarker(latlng, {
              radius: 5,
              color,
              weight: 2,
              fillColor: "#fff",
              fillOpacity: 1,
            });
        marker
          .bindTooltip(() => this._stopTooltip(route.route_id, station.order), {
            direction: "top",
            offset: [0, favorite ? -12 : -6],
            className: "tip",
          })
          .bindPopup(() => this._stopPopup(route.route_id, station.order), {
            className: "pop",
            offset: [0, favorite ? -8 : -2],
          })
          .on("popupopen", (ev) => {
            marker.closeTooltip();
            // Leaflet's auto-pan doesn't know about the overview panel.
            setTimeout(() => this._keepClearOfOverview(ev.popup), 0);
          })
          .addTo(this._stopLayer);
        marker._tagoStop = { routeId: route.route_id, order: station.order };
        this._stopMarkers.push(marker);
      }
    }

    if (this._config.show_legend && this._directions.size) {
      this._legend = L.control({ position: "bottomleft" });
      this._legend.onAdd = () => {
        const div = L.DomUtil.create("div", "panel legend");
        div.innerHTML = [...this._directions.values()]
          .map((d) => `<div><i style="background:${esc(d.color)}"></i>${esc(d.first_stop)} → ${esc(d.last_stop)}</div>`)
          .join("");
        return div;
      };
      this._legend.addTo(this._map);
    }
  }

  _busStates() {
    return Object.values(this._hass.states).filter(
      (s) =>
        s.entity_id.startsWith("geo_location.") &&
        s.attributes.source === "tago_bus" &&
        s.attributes.latitude !== undefined &&
        (!this._config.entry_id || s.attributes.config_entry_id === this._config.entry_id),
    );
  }

  _updateBuses() {
    if (!this._busLayer) return;
    const L = this._L;
    const seen = new Set();
    for (const state of this._busStates()) {
      const a = state.attributes;
      const id = state.entity_id;
      seen.add(id);
      const color = this._color(a.direction_index ?? 0);
      const html = `<div class="bus" style="background:${esc(color)}">${esc(a.route_no)}</div>`;
      const icon = () => L.divIcon({ className: "bus-icon", html, iconSize: [30, 30], iconAnchor: [15, 15] });
      let entry = this._busMarkers.get(id);
      if (!entry) {
        const marker = L.marker([a.latitude, a.longitude], { icon: icon(), zIndexOffset: 1000 })
          .bindTooltip(() => this._busTooltip(id), { direction: "top", offset: [0, -16], className: "tip" })
          .on("click", () => this._openMoreInfo(id))
          .addTo(this._busLayer);
        entry = { marker, html };
        this._busMarkers.set(id, entry);
      } else {
        entry.marker.setLatLng([a.latitude, a.longitude]);
        if (entry.html !== html) {
          entry.marker.setIcon(icon());
          entry.html = html;
        }
      }
    }
    for (const [id, entry] of this._busMarkers) {
      if (!seen.has(id)) {
        entry.marker.remove();
        this._busMarkers.delete(id);
      }
    }
  }

  _refreshOpenOverlays() {
    const markers = [...[...this._busMarkers.values()].map((e) => e.marker), ...this._stopMarkers];
    for (const marker of markers) {
      if (marker.isTooltipOpen()) marker.getTooltip().update();
    }
    // Rebuilding an open popup would wipe a pending button state, so only
    // refresh its live arrival rows.
    for (const marker of this._stopMarkers) {
      const popup = marker.getPopup();
      if (popup?.isOpen()) {
        const rows = popup.getElement()?.querySelector(".arrivals");
        const { routeId, order } = marker._tagoStop;
        if (rows) rows.innerHTML = this._arrivalRowsHtml(routeId, order);
      }
    }
  }

  // ---------------------------------------------------------------- arrivals

  /** Next buses for a stop: arrival API for the favorite, else bus positions. */
  _arrivalsFor(routeId, order) {
    const route = this._routes.get(routeId);
    if (!route) return { source: "none", buses: [] };

    if (route.favorite_order === order && route.arrival_entity_id) {
      const arrivals = this._hass.states[route.arrival_entity_id]?.attributes?.arrivals || [];
      if (arrivals.length) {
        return {
          source: "api",
          buses: arrivals.slice(0, 2).map((a) => ({
            minutes: a.minutes,
            stops: a.stops_remaining,
            note: a.vehicle_type,
          })),
        };
      }
    }

    const buses = this._busStates()
      .map((s) => s.attributes)
      .filter((a) => a.route_id === routeId && a.stop_order !== null && a.stop_order <= order)
      .map((a) => ({ minutes: null, stops: order - a.stop_order, note: a.vehicle_no }))
      .sort((x, y) => x.stops - y.stops)
      .slice(0, 2);
    return { source: "location", buses };
  }

  _arrivalRowsHtml(routeId, order) {
    const { source, buses } = this._arrivalsFor(routeId, order);
    if (!buses.length) return `<div class="row muted">다가오는 버스 없음</div>`;
    const rows = buses.map((bus, i) => {
      const main = formatMinutes(bus.minutes);
      const stops = formatStops(bus.stops);
      const label = i === 0 ? "이번 버스" : "다음 버스";
      const value = main
        ? `<span class="hl">${esc(main)}</span>${stops ? ` (${esc(stops)})` : ""}`
        : `<span class="hl">${esc(stops)}</span>`;
      return `<div class="row">${label} ${value} <span class="muted">${esc(bus.note || "")}</span></div>`;
    });
    if (source === "location") rows.push(`<div class="row muted">버스 위치 기준 계산</div>`);
    return rows.join("");
  }

  _stopHeaderHtml(route, station) {
    const direction = this._directions.get(station.direction_index);
    const star = route.favorite_order === station.order ? "★ " : "";
    return (
      `<b class="title">${star}${esc(station.name)}</b>` +
      `<div class="row muted">${esc(route.route_no)}번 · ${station.order}번째 · ${esc(direction?.label || "")}</div>`
    );
  }

  _findStation(routeId, order) {
    const route = this._routes.get(routeId);
    const station = route?.stations.find((s) => s.order === order);
    return route && station ? { route, station } : null;
  }

  _stopTooltip(routeId, order) {
    const found = this._findStation(routeId, order);
    if (!found) return "";
    return (
      this._stopHeaderHtml(found.route, found.station) +
      this._arrivalRowsHtml(routeId, order) +
      `<div class="row muted">눌러서 즐겨찾기 설정</div>`
    );
  }

  _stopPopup(routeId, order) {
    const found = this._findStation(routeId, order);
    const el = document.createElement("div");
    if (!found) return el;
    const { route, station } = found;
    const isFavorite = route.favorite_order === order;
    el.innerHTML =
      this._stopHeaderHtml(route, station) +
      `<div class="arrivals">${this._arrivalRowsHtml(routeId, order)}</div>` +
      `<button class="fav-btn ${isFavorite ? "on" : ""}">${
        isFavorite ? "즐겨찾기 해제" : "★ 즐겨찾는 정류장으로 설정"
      }</button>` +
      (!isFavorite && route.favorite_order !== null
        ? `<div class="row muted">이 노선의 기존 즐겨찾기를 대체합니다</div>`
        : "");
    const button = el.querySelector("button");
    this._L.DomEvent.on(button, "click", async (ev) => {
      this._L.DomEvent.stop(ev);
      button.disabled = true;
      button.textContent = "저장 중…";
      await this._setFavorite(route.entry_id, routeId, isFavorite ? null : order);
    });
    return el;
  }

  async _setFavorite(entryId, routeId, order) {
    try {
      const updated = await this._hass.callWS({
        type: "tago_bus/set_favorite",
        entry_id: entryId,
        route_id: routeId,
        order,
      });
      this._map.closePopup();
      this._setEntries(this._entries.map((e) => (e.entry_id === updated.entry_id ? updated : e)));
    } catch (err) {
      this._map.closePopup();
      this._showToast(`즐겨찾기를 저장하지 못했습니다: ${err.message || err.code || err}`);
    }
  }

  _busTooltip(entityId) {
    const state = this._hass.states[entityId];
    if (!state) return "운행 종료";
    const a = state.attributes;
    const rows = [
      `<b class="title">${esc(a.route_no)}번</b> <span class="muted">${esc(a.vehicle_no)}</span>`,
      `<div class="row">${esc(a.direction)}</div>`,
      `<div class="row">현재 <b>${esc(a.current_stop || "-")}</b>${a.next_stop ? ` → 다음 ${esc(a.next_stop)}` : ""}</div>`,
    ];
    const fav = a.favorite_stop;
    if (fav) {
      rows.push(`<div class="row">★ ${esc(fav.stop)}까지 <span class="hl">${esc(formatStops(fav.stops_remaining))}</span></div>`);
    }
    const distance = Number(state.state);
    const extras = [];
    if (state.state !== "" && !Number.isNaN(distance)) extras.push(`집에서 ${distance.toFixed(1)}km`);
    extras.push(`위치 갱신 ${formatAgo(state.last_updated)}`);
    rows.push(`<div class="row muted">${extras.join(" · ")}</div>`);
    return rows.join("");
  }

  // ---------------------------------------------------------------- overview

  _renderOverview(force = false) {
    if (!this._map || !this._routes) return;
    const L = this._L;
    if (!this._config.show_overview) {
      if (this._overview) {
        this._overview.remove();
        this._overview = undefined;
      }
      return;
    }
    if (!this._overview) {
      this._overview = L.control({ position: "topright" });
      this._overview.onAdd = () => {
        const div = L.DomUtil.create("div", "panel overview");
        div.innerHTML = `
          <button class="ov-head" type="button" aria-expanded="true">
            <span>🚌 도착 개요</span><span class="chev">▴</span>
          </button>
          <div class="ov-body"></div>`;
        L.DomEvent.disableClickPropagation(div);
        L.DomEvent.disableScrollPropagation(div);
        div.querySelector(".ov-head").addEventListener("click", () => {
          this._overviewOpen = !this._overviewOpen;
          writeOverviewOpen(this._overviewOpen);
          this._applyOverviewOpen();
        });
        div.querySelector(".ov-body").addEventListener("click", (ev) => {
          const item = ev.target.closest(".ov-route");
          if (item) this._focusStop(item.dataset.route, Number(item.dataset.order));
        });
        return div;
      };
      this._overview.addTo(this._map);
      this._overviewHtml = undefined;
      this._applyOverviewOpen();
    }

    const html = this._overviewBodyHtml();
    if (force || html !== this._overviewHtml) {
      this._overview.getContainer().querySelector(".ov-body").innerHTML = html;
      this._overviewHtml = html;
    }
  }

  _applyOverviewOpen() {
    const div = this._overview?.getContainer();
    if (!div) return;
    div.classList.toggle("closed", !this._overviewOpen);
    div.querySelector(".ov-head").setAttribute("aria-expanded", String(this._overviewOpen));
  }

  _overviewBodyHtml() {
    const multipleRoutes = this._routes.size > 1;
    const blocks = [];
    for (const route of this._routes.values()) {
      const variant = multipleRoutes ? ` <span class="muted">${esc(route.start_stop)}→${esc(route.end_stop)}</span>` : "";
      if (route.favorite_order === null || route.favorite_order === undefined) {
        blocks.push(
          `<div class="ov-route" style="border-left-color:${esc(this._color(route.directions[0]?.index ?? 0))}">` +
            `<div class="name">${esc(route.route_no)}번${variant}</div>` +
            `<div class="muted">지도에서 정류장을 눌러 ★ 즐겨찾기를 설정하세요</div></div>`,
        );
        continue;
      }
      const station = route.stations.find((s) => s.order === route.favorite_order);
      if (!station) continue;
      const direction = this._directions.get(station.direction_index);
      const { source, buses } = this._arrivalsFor(route.route_id, station.order);
      const rows = buses.length
        ? buses
            .map((bus, i) => {
              const minutes = formatMinutes(bus.minutes);
              const stops = formatStops(bus.stops);
              return (
                `<div class="ov-bus"><span>${i === 0 ? "이번" : "다음"}</span>` +
                `<span><span class="min">${esc(minutes || stops)}</span>` +
                `${minutes && stops ? ` <span class="muted">${esc(stops)}</span>` : ""}</span></div>`
              );
            })
            .join("")
        : `<div class="muted">다가오는 버스 없음</div>`;
      blocks.push(
        `<div class="ov-route" data-route="${esc(route.route_id)}" data-order="${station.order}" ` +
          `style="border-left-color:${esc(this._color(station.direction_index))}">` +
          `<div class="name">${esc(route.route_no)}번 · ★ ${esc(station.name)}${variant}</div>` +
          `<div class="muted">${esc(direction?.label || "")}${source === "location" && buses.length ? " · 위치 기준" : ""}</div>` +
          rows +
          `</div>`,
      );
    }
    return blocks.join("") || `<div class="ov-route muted">표시할 노선이 없습니다</div>`;
  }

  _keepClearOfOverview(popup) {
    const panel = this._overview?.getContainer();
    const box = popup.getElement()?.querySelector(".leaflet-popup-content-wrapper");
    if (!panel || !box || !popup.isOpen()) return;
    const p = box.getBoundingClientRect();
    const o = panel.getBoundingClientRect();
    const overlaps = p.top < o.bottom && p.right > o.left && p.left < o.right;
    if (overlaps) this._map.panBy([0, -(o.bottom + 10 - p.top)]);
  }

  _focusStop(routeId, order) {
    const marker = this._stopMarkers.find(
      (m) => m._tagoStop.routeId === routeId && m._tagoStop.order === order,
    );
    if (!marker) return;
    // No animation: popup auto-pan is skipped while the map is still moving.
    this._map.setView(marker.getLatLng(), Math.max(this._map.getZoom(), 16), { animate: false });
    marker.fire("click", { latlng: marker.getLatLng() });
  }

  // ---------------------------------------------------------------- misc

  _fitBounds() {
    const points = [];
    for (const route of this._routes.values()) {
      for (const s of route.stations) points.push([s.latitude, s.longitude]);
    }
    for (const s of this._busStates()) points.push([s.attributes.latitude, s.attributes.longitude]);
    if (points.length) this._map.fitBounds(points, { padding: [24, 24] });
  }

  _isDark() {
    if (this._config.dark_mode === "dark") return true;
    if (this._config.dark_mode === "light") return false;
    return Boolean(this._hass.themes?.darkMode);
  }

  _syncTheme() {
    // OSM has no dark tiles, so invert the light ones.
    this._container.classList.toggle("dark", this._isDark());
  }

  _openMoreInfo(entityId) {
    this.dispatchEvent(
      new CustomEvent("hass-more-info", { detail: { entityId }, bubbles: true, composed: true }),
    );
  }

  _showToast(message) {
    this.dispatchEvent(
      new CustomEvent("hass-notification", { detail: { message }, bubbles: true, composed: true }),
    );
  }

  _showMessage(text) {
    if (!this._message) return;
    this._message.hidden = !text;
    this._message.textContent = text || "";
  }
}

if (!customElements.get("tago-bus-map-card")) {
  customElements.define("tago-bus-map-card", TagoBusMapCard);
  window.customCards = window.customCards || [];
  window.customCards.push({
    type: "tago-bus-map-card",
    name: "TAGO Bus Map",
    description: "버스 노선, 정류장, 실시간 버스 위치와 즐겨찾는 정류장 도착 정보를 지도에 표시합니다.",
  });
  console.info(`%c TAGO-BUS-MAP-CARD %c ${CARD_VERSION} `, "background:#1e88e5;color:#fff", "");
}
