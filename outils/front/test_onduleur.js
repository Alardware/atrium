/* La carte Onduleur, telle que Home Assistant la nourrit — ici un EcoFlow. */
const { JSDOM } = require('jsdom');
const fs = require('fs');
const path = require('path');
const cible = process.argv[2] || path.join(__dirname, '..', '..', 'app', 'static', 'index.html');
const dom = new JSDOM(fs.readFileSync(cible, 'utf8'), {
  runScripts: 'dangerously', resources: 'usable',
  url: 'file:///' + cible, pretendToBeVisual: true });
const w = dom.window;
const ERREURS = []; w.addEventListener('error', (e) => ERREURS.push(String(e.message)));
w.WebSocket = function () { this.close = () => {}; this.send = () => {}; };
w.fetch = () => Promise.reject(new Error('hors ligne'));
const inj = (c) => { const s = w.document.createElement('script'); s.textContent = c; w.document.body.appendChild(s); s.remove(); };
const $ = (id) => w.document.getElementById(id);
const attends = (ms) => new Promise((r) => setTimeout(r, ms));

const e = (id, etat, attrs) => ({ entity_id: id, state: String(etat), attributes: attrs || {} });

/* Un EcoFlow Delta 2, tel que l'intégration le publie. */
const ECOFLOW = [
  e('sensor.delta_2_battery_level', 78, { friendly_name: 'Delta 2 Battery Level',
                                          device_class: 'battery', unit_of_measurement: '%' }),
  e('sensor.delta_2_remaining_time', 214, { friendly_name: 'Delta 2 Remaining Time',
                                            device_class: 'duration', unit_of_measurement: 'min' }),
  e('sensor.delta_2_input_power', 0, { friendly_name: 'Delta 2 Input Power',
                                       device_class: 'power', unit_of_measurement: 'W' }),
  e('sensor.delta_2_output_power', 143, { friendly_name: 'Delta 2 Output Power',
                                          device_class: 'power', unit_of_measurement: 'W' }),
  e('binary_sensor.delta_2_ac_in', 'off', { friendly_name: 'Delta 2 AC In',
                                            device_class: 'plug' }),
  /* du bruit : d'autres capteurs de la maison, qui ne doivent pas s'y mêler */
  e('sensor.salon_temperature', 21.4, { friendly_name: 'Salon', device_class: 'temperature',
                                        unit_of_measurement: '°C' }),
  e('sensor.lave_linge_power', 1240, { friendly_name: 'Lave-linge', device_class: 'power',
                                       unit_of_measurement: 'W' }),
  e('sensor.telephone_battery', 64, { friendly_name: 'Téléphone', device_class: 'battery',
                                      unit_of_measurement: '%' }),
];

const carte = () => w.document.querySelector('#featured [data-w="onduleur"]');
const lire = () => {
  const c = carte();
  if (!c) return '(aucune carte)';
  const grand = c.querySelector('.cock-big');
  const lignes = [...c.querySelectorAll('.cock-row, .c-row')].map(
    (r) => r.textContent.replace(/\s+/g, ' ').trim());
  return (grand ? grand.textContent.replace(/\s+/g, ' ').trim() : '')
    + ' | ' + lignes.join('  ·  ');
};

setTimeout(async () => {
  inj('apps = []; CONN = "ok"; STATES = ' + JSON.stringify(ECOFLOW) + ';'
    + 'WIDGETS = ["onduleur"]; EDIT_W = false; i18n.langue = "fr";'
    + 'i18n.appliquer(document); appliquerDisposition();');
  await attends(250);

  console.log('=== sur batterie, secteur coupé ===');
  console.log('    ' + lire());
  console.log('    carte en alerte : ' + (carte() && carte().classList.contains('w-alerte')));

  console.log('\n=== le secteur revient ===');
  inj('STATES = STATES.map(function (s) {'
    + '  if (s.entity_id === "binary_sensor.delta_2_ac_in") return Object.assign({}, s, { state: "on" });'
    + '  if (s.entity_id === "sensor.delta_2_input_power") return Object.assign({}, s, { state: "320" });'
    + '  return s; }); appliquerDisposition();');
  await attends(200);
  console.log('    ' + lire());

  console.log('\n=== batterie basse et secteur coupé : la carte se teinte ===');
  inj('STATES = STATES.map(function (s) {'
    + '  if (s.entity_id === "binary_sensor.delta_2_ac_in") return Object.assign({}, s, { state: "off" });'
    + '  if (s.entity_id === "sensor.delta_2_input_power") return Object.assign({}, s, { state: "0" });'
    + '  if (s.entity_id === "sensor.delta_2_battery_level") return Object.assign({}, s, { state: "12" });'
    + '  if (s.entity_id === "sensor.delta_2_remaining_time") return Object.assign({}, s, { state: "26" });'
    + '  return s; }); appliquerDisposition();');
  await attends(200);
  console.log('    ' + lire());
  console.log('    carte en alerte : ' + (carte() && carte().classList.contains('w-alerte')));

  console.log('\n=== sans onduleur, la carte ne se propose pas ===');
  inj('STATES = STATES.filter(function (s) { return s.entity_id.indexOf("delta_2") === -1; });'
    + 'window.__d = REGISTRE_W.onduleur.dispo(); appliquerDisposition();');
  await attends(200);
  console.log('    proposée : ' + w.__d + '   carte présente : ' + !!carte());
  console.log('    (le téléphone et le lave-linge ne suffisent pas)');

  console.log('\nerreurs page :', ERREURS.length ? ERREURS : 'aucune');
  process.exit(0);
}, 2000);
