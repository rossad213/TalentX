const fs = require('fs');
const vm = require('vm');
const assert = require('assert');

const DAY = 24 * 60 * 60 * 1000;
const now = Date.now();
const week2 = now - 6 * DAY;
const monday = now - 2 * DAY;

const context = {
  console,
  Math,
  Number,
  String,
  Array,
  Set,
  Map,
  Date,
  window: {},
  chartRange: '1M',
  CHART_RANGE_CONFIG: {
    '1D': {duration: 8 * 60 * 60 * 1000, points: 36},
    '5D': {duration: 5 * DAY, points: 48},
    '1M': {duration: 30 * DAY, points: 54},
    '1Y': {duration: 365 * DAY, points: 72}
  },
  localPrice: record => Number(record.marketPrice || 1),
};
vm.createContext(context);

vm.runInContext(fs.readFileSync('chart-history.js', 'utf8'), context, {filename: 'chart-history.js'});
vm.runInContext(fs.readFileSync('assets/event-chart-safety.js', 'utf8'), context, {filename: 'event-chart-safety.js'});

const record = {
  primaryCategory: 'Athlete',
  leagueOrMedium: 'NFL',
  marketPrice: 150,
  priceHistoryStatus: 'source-backed-full-point-in-time-nfl-replay',
  priceEvents: [],
  priceHistory: [
    {
      time: new Date(week2 - 1000).toISOString(),
      price: 140,
      eventId: 'espn:week2',
      phase: 'open',
      historyType: 'verified-event-replay',
      source: 'verified-nfl-event-replay'
    },
    {
      time: new Date(week2).toISOString(),
      price: 144,
      eventId: 'espn:week2',
      phase: 'close',
      historyType: 'verified-event-replay',
      source: 'verified-nfl-event-replay'
    },
    {
      time: new Date(monday - 1000).toISOString(),
      price: 144,
      eventId: 'espn:monday',
      phase: 'open',
      historyType: 'verified-event-replay',
      source: 'verified-nfl-event-replay'
    },
    {
      time: new Date(monday).toISOString(),
      price: 150,
      eventId: 'espn:monday',
      phase: 'close',
      historyType: 'verified-event-replay',
      source: 'verified-nfl-event-replay'
    }
  ]
};

const series = context.chartSeries(record, '1M');
assert(series.length >= 6, 'event-aligned series should include boundaries and exact event points');
assert(series.some(point => Math.abs(Number(point.time) - week2) <= 2), 'Week 2 close timestamp must be plotted exactly');
assert(series.some(point => Math.abs(Number(point.time) - monday) <= 2), 'Monday close timestamp must be plotted exactly');
assert.strictEqual(series[series.length - 1].value, 150, 'replay should end at the current anchored price');

const week2Point = series.find(point => Math.abs(Number(point.time) - week2) <= 2);
const mondayPoint = series.find(point => Math.abs(Number(point.time) - monday) <= 2);
assert.strictEqual(week2Point.value, 144);
assert.strictEqual(mondayPoint.value, 150);

console.log('NFL event-aligned chart replay test passed.');
