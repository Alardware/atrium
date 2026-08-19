/* Le dessin d'une source de lecture : ce que le nom donne vraiment. */
const { JSDOM } = require('jsdom');
const fs = require('fs');
const path = require('path');
const cible = path.join(__dirname, '..', '..', 'app', 'static', 'index.html');
const dom = new JSDOM(fs.readFileSync(cible, 'utf8'), {
  runScripts: 'dangerously', resources: 'usable',
  url: 'file:///' + cible, pretendToBeVisual: true });
const w = dom.window;
w.WebSocket = function () { this.close = () => {}; this.send = () => {}; };
w.fetch = () => Promise.reject(new Error('hors ligne'));
setTimeout(() => {
  const s = w.document.createElement('script');
  s.textContent = `window.__r = ['Free TV','OQEE by Free','YouTube','YouTube Music',
    'Netflix','Disney+','Prime Video','Apple TV','Twitch','Spotify','Molotov','']
    .map(function (n) { return n + '  ->  ' + (iconeSource(n) || '(rien)'); });`;
  w.document.body.appendChild(s);
  w.__r.forEach((l) => console.log('  ' + l.replace(
    'https://cdn.jsdelivr.net/gh/homarr-labs/dashboard-icons/svg/', '')));
  process.exit(0);
}, 2000);
