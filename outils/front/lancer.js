/* Les suites d'interface, lancées l'une après l'autre.

   Le serveur a douze contrôles qui tournent à chaque poussée ; l'interface,
   qui pèse pourtant les deux tiers du code, n'en avait aucun. Ces suites
   montent la vraie page dans jsdom, lui injectent des données, et regardent ce
   qu'elle en fait.

   Chacune écrit ce qu'elle constate, ligne à ligne. Ce lanceur ne juge pas à
   leur place : il échoue si une suite sort en erreur, si elle a imprimé un
   ECHEC, ou si la page a levé une exception pendant l'essai — ce dernier point
   compte autant que le reste, une page qui casse en silence ayant l'air de
   fonctionner.

   Usage :
       npm install --no-save jsdom axe-core
       node outils/front/lancer.js [nom-de-suite]
*/
const { spawnSync } = require('child_process');
const fs = require('fs');
const path = require('path');

const ICI = __dirname;
const choisie = process.argv[2] || '';

const suites = fs.readdirSync(ICI)
  .filter((f) => f.endsWith('.js') && f !== 'lancer.js')
  .filter((f) => !choisie || f.includes(choisie))
  .sort();

if (!suites.length) {
  console.error('aucune suite à lancer' + (choisie ? ' pour « ' + choisie + ' »' : ''));
  process.exit(1);
}

console.log(suites.length + ' suite(s) d\'interface\n');

let rates = 0;
for (const suite of suites) {
  const debut = Date.now();
  const r = spawnSync(process.execPath, [path.join(ICI, suite)], {
    encoding: 'utf8', timeout: 120000,
  });
  const sortie = (r.stdout || '') + (r.stderr || '');
  const echecs = (sortie.match(/ECHEC/g) || []).length;
  /* « erreurs page : aucune » va bien ; « erreurs page : [ … ] » non. */
  const cassee = /erreurs page\s*:\s*\[/.test(sortie);
  const bon = r.status === 0 && !echecs && !cassee && !r.error;
  if (!bon) rates += 1;

  const raison = r.error ? String(r.error.message)
    : r.status !== 0 ? 'sortie ' + r.status
    : echecs ? echecs + ' point(s) en échec'
    : cassee ? 'la page a levé une exception' : '';
  /* console.log ne connaît pas les largeurs de printf : on aligne à la main. */
  console.log((bon ? '  ok    ' : '  ECHEC ')
    + suite.replace(/\.js$/, '').padEnd(22)
    + (((Date.now() - debut) / 1000).toFixed(1) + ' s').padStart(7)
    + (raison ? '   ' + raison : ''));
  if (!bon) {
    console.log(sortie.split('\n').map((l) => '        ' + l).join('\n'));
  }
}

console.log();
if (rates) {
  console.log(rates + ' suite(s) en échec.');
  process.exit(1);
}
console.log("l'interface se comporte comme les suites le décrivent.");
