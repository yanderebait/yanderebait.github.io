// Shared by shop.html and admin.html, so the two pages can never disagree
// about what a status is called or how a price is written.
(function () {
  'use strict';

  var BUCKET = 'media';
  var MAX_FILE_BYTES = 50 * 1024 * 1024;        // Supabase free plan: 50 MB per file
  var STORAGE_BYTES = 1000 * 1024 * 1024;       // Supabase free plan: 1 GB in total

  // In the order the shop shows them.
  var STATUSES = [
    { id: 'for_sale',     label: 'For sale' },
    { id: 'reserved',     label: 'Reserved' },
    { id: 'coming_soon',  label: 'Coming soon' },
    { id: 'not_for_sale', label: 'Not for sale' },
    { id: 'sold',         label: 'Sold' },
    { id: 'draft',        label: 'Draft' }      // never shown in the shop
  ];

  function statusLabel(id) {
    for (var i = 0; i < STATUSES.length; i++) if (STATUSES[i].id === id) return STATUSES[i].label;
    return id;
  }

  function statusRank(id) {
    for (var i = 0; i < STATUSES.length; i++) if (STATUSES[i].id === id) return i;
    return STATUSES.length;
  }

  // 4500 -> "£45", 4550 -> "£45.50", null -> "Price on request"
  function formatPrice(pence) {
    if (pence === null || pence === undefined) return 'Price on request';
    var pounds = Math.floor(pence / 100), rest = pence % 100;
    var whole = String(pounds).replace(/\B(?=(\d{3})+(?!\d))/g, ',');
    return '£' + whole + (rest ? '.' + (rest < 10 ? '0' : '') + rest : '');
  }

  // "45" -> 4500, "£45.50" -> 4550, "" -> null, anything else -> NaN
  function parsePrice(text) {
    var t = String(text).replace(/[£,\s]/g, '');
    if (t === '') return null;
    if (!/^\d{1,7}(\.\d{1,2})?$/.test(t)) return NaN;
    var parts = t.split('.');
    var pence = parseInt(parts[0], 10) * 100;
    if (parts[1]) pence += parseInt((parts[1] + '0').slice(0, 2), 10);
    return pence;
  }

  function formatBytes(bytes) {
    if (bytes < 1024 * 1024) return Math.max(1, Math.round(bytes / 1024)) + ' KB';
    var mb = bytes / (1024 * 1024);
    return (mb < 10 ? mb.toFixed(1) : String(Math.round(mb))) + ' MB';
  }

  function config() {
    var c = window.SHOP_CONFIG || {};
    return { url: String(c.supabaseUrl || '').replace(/\/+$/, ''), key: String(c.supabaseKey || '') };
  }

  // Returns the Supabase client, or null while the shop isn't connected yet.
  function connect() {
    var c = config();
    if (!c.url || !c.key || !window.supabase) return null;
    return window.supabase.createClient(c.url, c.key);
  }

  // Public address of an uploaded file.
  function mediaUrl(path) {
    if (!path) return '';
    return config().url + '/storage/v1/object/public/' + BUCKET + '/' +
      String(path).split('/').map(encodeURIComponent).join('/');
  }

  window.Shop = {
    BUCKET: BUCKET, MAX_FILE_BYTES: MAX_FILE_BYTES, STORAGE_BYTES: STORAGE_BYTES, STATUSES: STATUSES,
    statusLabel: statusLabel, statusRank: statusRank,
    formatPrice: formatPrice, parsePrice: parsePrice, formatBytes: formatBytes,
    connect: connect, mediaUrl: mediaUrl
  };
})();
