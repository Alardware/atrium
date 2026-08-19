/* La carte Solaire, sur les entités d'un micro-onduleur de balcon.

   Un PowerStream publie des watts, des volts, des ampères et des températures
   par panneau, plus deux entités de batterie qui restent « unavailable » quand
   aucune batterie n'est branchée. Il ne faut donc ni le prendre pour un
   onduleur, ni additionner ses tensions avec ses puissances.
*/
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
const attends = (ms) => new Promise((r) => setTimeout(r, ms));
const dit = (bon, titre, detail) =>
  console.log((bon ? '  ok    ' : '  ECHEC ') + titre + (detail ? '   ' + detail : ''));

const e = (id, etat, attrs) => ({ entity_id: id, state: String(etat), attributes: attrs || {} });
const W = (u) => ({ device_class: 'power', unit_of_measurement: 'W' });
const P = { device_class: 'voltage', unit_of_measurement: 'V' };
const A = { device_class: 'current', unit_of_measurement: 'A' };
const WH = { device_class: 'energy', unit_of_measurement: 'Wh', state_class: 'total_increasing' };

/* Relevé réel d'un PowerStream, doublons « unavailable » compris. */
const ETATS = [
  e('sensor.powerstream_solar_1_watts_2', 15, W()),
  e('sensor.powerstream_solar_2_watts_2', 19, W()),
  e('sensor.powerstream_inverter_output_watts_2', 33, W()),
  e('sensor.powerstream_battery_input_watts_2', 0, W()),
  e('sensor.powerstream_solar_1_input_potential_2', 29.4, P),
  e('sensor.powerstream_solar_1_current', 0.4, A),
  e('sensor.powerstream_solar_1_temperature_2', 34, { device_class: 'temperature', unit_of_measurement: '°C' }),
  e('sensor.powerstream_pv1_today_energy_total', 2, WH),
  e('sensor.powerstream_pv2_today_energy_total', 3, WH),
  e('sensor.powerstream_battery_charge', 'unavailable', { device_class: 'battery', unit_of_measurement: '%' }),
  e('sensor.powerstream_battery_charge_2', 'unknown', { device_class: 'battery', unit_of_measurement: '%' }),
  e('sensor.powerstream_solar_1_watts', 'unavailable', W()),
  e('sensor.lave_linge_power', 1240, W()),
];

const carte = () => w.document.querySelector('#featured [data-w="solaire"]');
const lire = () => {
  const c = carte();
  if (!c) return '(aucune carte)';
  const g = c.querySelector('.cock-big');
  const lignes = [...c.querySelectorAll('.cock-row, .c-row')]
    .map((r) => r.textContent.replace(/\s+/g, ' ').trim());
  return (g ? g.textContent.replace(/\s+/g, ' ').trim() : '') + ' | ' + lignes.join('  ·  ');
};

setTimeout(async () => {
  inj('apps = []; CONN = "ok"; STATES = ' + JSON.stringify(ETATS) + ';'
    + 'WIDGETS = ["solaire", "onduleur"]; EDIT_W = false; i18n.langue = "fr";'
    + 'i18n.appliquer(document); appliquerDisposition();'
    + 'window.__ondu = REGISTRE_W.onduleur.dispo(); window.__sol = REGISTRE_W.solaire.dispo();');
  await attends(250);

  console.log('=== un micro-onduleur qui produit ===');
  console.log('    ' + lire());
  dit(w.__sol === true, 'la carte Solaire se propose');
  dit(w.__ondu === false,
      'la carte Onduleur ne se propose pas : aucune batterie ne répond');
  dit(lire().startsWith('34 W'), 'la production additionne les deux panneaux', lire().split('|')[0]);
  dit(lire().includes('33 W'), 'et l\'injection est distincte de la production');
  dit(lire().includes('5 Wh'), 'le total du jour vient du service, pas d\'un calcul');

  console.log('\n=== ce qui n\'est pas de la production reste dehors ===');
  dit(!lire().includes('29') && !lire().includes('0.4'),
      'ni les tensions ni les courants n\'entrent dans le compte');
  dit(!lire().includes('1240'), 'et le lave-linge non plus');

  console.log('\n=== une batterie qui répond change la donne ===');
  inj('STATES = STATES.map(function (s) {'
    + ' if (s.entity_id === "sensor.powerstream_battery_charge_2")'
    + '   return Object.assign({}, s, { state: "64" });'
    + ' return s; }); window.__ondu2 = REGISTRE_W.onduleur.dispo(); appliquerDisposition();');
  await attends(200);
  dit(w.__ondu2 === true, 'la carte Onduleur se propose alors, elle aussi');

  console.log('\nerreurs page :', ERREURS.length ? ERREURS : 'aucune');
  process.exit(0);
}, 2000);
