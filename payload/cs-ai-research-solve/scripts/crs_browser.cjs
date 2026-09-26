'use strict';
// Offline, actual-page verification. No downloads, remote service or CDN.
const fs = require('fs'), path = require('path'), crypto = require('crypto');
const {pathToFileURL} = require('url');
const opts = {};
for (let i=2;i<process.argv.length;i+=2) {
  if (!['--site','--out','--playwright-module','--channel','--workers'].includes(process.argv[i]) || !process.argv[i+1]) throw Error('Invalid browser-check argument.');
  opts[process.argv[i]]=process.argv[i+1];
}
const hash=b=>crypto.createHash('sha256').update(b).digest('hex');
const site=path.resolve(opts['--site']||''), out=path.resolve(opts['--out']||'');
if (!opts['--site'] || !opts['--out'] || fs.existsSync(out)) throw Error('Use an existing reading site and a new report directory.');
const {chromium}=require(opts['--playwright-module']||'playwright');
const manifestBytes=fs.readFileSync(path.join(site,'build-manifest.json'));
const manifest=JSON.parse(manifestBytes), contract=JSON.parse(fs.readFileSync(path.join(site,'reading-contract.json')));
const all=[];
for (const filename of Object.keys(manifest.files).filter(x=>x.endsWith('.html') && !x.includes('/') && x!=='00-打开研究地图.html').sort()) {
  const text=fs.readFileSync(path.join(site,filename),'utf8');
  const match=text.match(/<script[^>]*id="language-content"[^>]*>([\s\S]*?)<\/script>/);
  if (!match) throw Error('Missing page language payload: '+filename);
  const bodies=JSON.parse(match[1]);
  const hero=(text.match(/<h1 class="hero">([\s\S]*?)<\/h1>/)||[])[1]||'';
  const title_count=(hero.match(/data-tex=/g)||[]).length;
  for (const [lang,body] of Object.entries(bodies)) {
    const count=(body.match(/data-tex=/g)||[]).length;
    if (count || title_count || contract.manuscript_pages.includes(filename.slice(0,-5)) || filename==='index.html') all.push({filename,lang,count,title_count,body_sha256:hash(body),body,hero});
  }
}
// A site whose notation review declares formula prose cannot pass with zero formulas.
const formulaProse=contract.notation_review.page_reviews.some(x=>x.formula_prose===true);
if (!all.length || (formulaProse && !all.some(x=>x.count>0))) throw Error('A reading site with declared formula prose cannot pass with zero formulas.');
fs.mkdirSync(out,{recursive:false});
const report={schema:'crs-browser-check/v1',snapshot:manifest.snapshot,manifest_sha256:hash(manifestBytes),browser:null,network_requests:[],page_languages:[],checks:{},screenshots:[],started_at:new Date().toISOString(),complete:false};
let browser;
const urlFor=(filename,lang)=>pathToFileURL(path.join(site,filename)).href+'?lang='+lang;
async function ready(page) {
  await page.waitForFunction(()=>document.body.dataset.formulaReady==='true',{},{timeout:60000});
}
async function inspectFormulaLayout(page) {
  await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
  return page.evaluate(() => {
    const failures=[];
    for (const el of document.querySelectorAll('#content .formula[data-tex], .hero .formula[data-tex]')) {
      const svg=el.querySelector('svg');if(!svg)continue;
      let block=el.parentElement;
      while(block && ['inline','contents'].includes(getComputedStyle(block).display))block=block.parentElement;
      if(!block || !block.offsetWidth)continue;
      const style=getComputedStyle(block),scale=block.getBoundingClientRect().width/block.offsetWidth;
      const room=(block.clientWidth-parseFloat(style.paddingLeft||0)-parseFloat(style.paddingRight||0))*scale;
      const width=svg.getBoundingClientRect().width, css=getComputedStyle(el),marked=el.dataset.overflow==='true';
      if(room<=0 || width<=0)continue;
      if(marked && width<=room+1)failures.push('unnecessary-scroll-hint');
      if(!marked && width>room+1)failures.push('unhandled-wide-formula');
      if(!marked && el.dataset.display!=='true' && (css.display!=='inline'||css.overflowX!=='visible'))failures.push('short-inline-scroll-container');
      if(!marked && el.hasAttribute('data-scroll-hint'))failures.push('stale-scroll-hint');
    }
    if(document.documentElement.scrollWidth>innerWidth+2)failures.push('page-overflow');
    return failures;
  });
}
async function capture(page,name) {
  const filename=name+'.png';await page.screenshot({path:path.join(out,filename),fullPage:false});
  report.screenshots.push({file:filename,sha256:hash(fs.readFileSync(path.join(out,filename))),url:page.url().split('/').pop(),viewport:page.viewportSize()});
}
(async()=>{
  browser=await chromium.launch({channel:opts['--channel']||'msedge',headless:true});report.browser=browser.version();
  const context=await browser.newContext({viewport:{width:1360,height:960}});
  await context.route(/https?:\/\//,route=>{report.network_requests.push(route.request().url());return route.abort();});
  let next=0;
  const workers=Math.max(1,Math.min(4,Number(opts['--workers']||3)));
  await Promise.all(Array.from({length:workers},async()=>{
    const page=await context.newPage();
    while (next<all.length) {
      const item=all[next++], errors=[];
      const listener=e=>errors.push(String(e));page.on('pageerror',listener);
      try {
        await page.goto(urlFor(item.filename,item.lang),{waitUntil:'load',timeout:60000});await ready(page);
        const result=await page.evaluate(({body,lang,hero})=>{
          const expected=document.createElement('div');expected.innerHTML=body;
          const actual=document.querySelector('#content');
          function canonical(node) {
            if (node.nodeType===Node.TEXT_NODE) return ['text',node.data];
            if (node.nodeType!==Node.ELEMENT_NODE) return ['other',node.nodeType,node.textContent];
            const attrs=[...node.attributes].filter(a=>!['data-overflow','data-scroll-hint'].includes(a.name)).map(a=>{
              let value=a.value;
              if(a.name==='href') {const url=new URL(value,location.href);url.searchParams.delete('lang');value=url.href;}
              return [a.name,value];
            }).sort((a,b)=>a[0].localeCompare(b[0]));
            return [node.tagName,attrs,node.matches('.formula[data-tex]') ? [] : [...node.childNodes].map(canonical)];
          }
          const formulas=root=>[...root.querySelectorAll('.formula[data-tex]')].map(x=>[x.dataset.tex,x.dataset.display==='true']);
          const titleNodes=[...document.querySelectorAll('.hero .formula[data-tex]')];
          const expectedTitle=document.createElement('div');expectedTitle.innerHTML=hero;
          const titleEqual=JSON.stringify([...expectedTitle.childNodes].map(canonical))===JSON.stringify([...document.querySelector('.hero').childNodes].map(canonical));
          return {title_identity_equal:titleEqual,title_marked:titleNodes.length,title_rendered:titleNodes.filter(x=>x.querySelector('svg')).length,errors:Number(document.body.dataset.formulaErrors),marked:formulas(actual).length,rendered:[...actual.querySelectorAll('.formula[data-tex]')].filter(x=>x.querySelector('svg')).length,merrors:actual.querySelectorAll('[data-mml-node="merror"],[data-mjx-error]').length,overflow:document.documentElement.scrollWidth>innerWidth+2,activeLanguage:actual.lang,
            dom_equal:JSON.stringify([...expected.childNodes].map(canonical))===JSON.stringify([...actual.childNodes].map(canonical)),formula_identity_equal:JSON.stringify(formulas(expected))===JSON.stringify(formulas(actual))};
        },{body:item.body,lang:item.lang,hero:item.hero});
        result.layout_errors=await inspectFormulaLayout(page);
        await page.setViewportSize({width:390,height:844});
        result.narrow_layout_errors=await inspectFormulaLayout(page);
        await page.setViewportSize({width:1360,height:960});
        const {body,hero,...binding}=item;
        report.page_languages.push({...binding,...result,script_errors:errors});
        if (!result.title_identity_equal || result.title_marked!==item.title_count || result.title_rendered!==item.title_count || result.errors || result.merrors || result.marked!==item.count || result.rendered!==item.count || !result.dom_equal || !result.formula_identity_equal || result.activeLanguage!==(item.lang==='zh'?'zh-CN':'en') || errors.length || result.overflow || result.layout_errors.length || result.narrow_layout_errors.length) throw Error('Page rendering, exact content, language or layout failed: '+item.filename+'/'+item.lang);
      } finally {page.off('pageerror',listener);}
    }
    await page.close();
  }));
  const page=await context.newPage();
  await page.goto(urlFor('index.html','zh'));await ready(page);
  // Repeated geometry changes must not let a hint influence subsequent widths.
  report.checks.inline_formula_reflow=true;
  for(const width of [390,1360,600,1360]) {
    await page.setViewportSize({width,height:960});
    if((await inspectFormulaLayout(page)).length)report.checks.inline_formula_reflow=false;
  }
  await page.evaluate(()=>{document.body.style.zoom='1.25';window.dispatchEvent(new Event('resize'));});
  report.checks.formula_zoom=(await inspectFormulaLayout(page)).length===0;
  await page.evaluate(()=>{document.body.style.zoom='';window.dispatchEvent(new Event('resize'));});
  report.checks.lazy_search=await page.evaluate(()=>!window.CRS_SEARCH&&!document.querySelector('script[src="search-index.js"]'));
  await capture(page,'home-desktop');
  await page.setViewportSize({width:390,height:844});await capture(page,'home-narrow');
  report.checks.narrow_home=await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+2);
  await page.setViewportSize({width:794,height:1123});await page.emulateMedia({media:'print'});await capture(page,'home-print');
  report.checks.print_readable=await page.evaluate(()=>getComputedStyle(document.querySelector('main')).display!=='none'&&getComputedStyle(document.querySelector('#content')).display!=='none');
  await page.emulateMedia({media:'screen'});await page.setViewportSize({width:1360,height:960});
  const manuscript=contract.manuscript_pages[0];if (!manuscript) throw Error('Complete manuscript inventory is empty.');
  await page.locator('#search').fill(manuscript.split('-')[0]);
  await page.waitForFunction(()=>Array.isArray(window.CRS_SEARCH));
  await page.waitForTimeout(250);
  report.checks.search=await page.locator('#results a').count()>0;
  await page.goto(urlFor(manuscript+'.html','en'));await ready(page);
  await page.locator('[data-language="zh"]').click();await ready(page);await page.reload();await ready(page);
  report.checks.language_reload=await page.evaluate(()=>new URLSearchParams(location.search).get('lang')==='zh'&&document.querySelector('#content').lang==='zh-CN'&&localStorage.getItem('crs-reading-language')==='zh');
  await page.locator('aside .brand').click();await ready(page);
  report.checks.language_navigation=await page.evaluate(()=>new URLSearchParams(location.search).get('lang')==='zh'&&document.querySelector('#content').lang==='zh-CN');
  const anchor=page.locator('#content a[href^="#home-"]').last();await anchor.click();await page.waitForTimeout(100);
  report.checks.contents=await page.evaluate(()=>{const el=document.getElementById(decodeURIComponent(location.hash.slice(1)));return Boolean(el)&&Math.abs(el.getBoundingClientRect().top)<innerHeight;});
  const complex=[...all].filter(x=>contract.manuscript_pages.includes(x.filename.slice(0,-5))).sort((a,b)=>b.count-a.count)[0];
  if (complex) {
    await page.goto(urlFor(complex.filename,'en'));await ready(page);await capture(page,'manuscript-desktop');
    await page.setViewportSize({width:390,height:844});await capture(page,'manuscript-narrow');
    report.checks.narrow_manuscript=await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+2);
    await page.locator('[data-language="zh"]').click();await ready(page);await capture(page,'manuscript-narrow-zh');
  }
  report.checks.no_network=report.network_requests.length===0;
  report.expected_page_languages=all.length;
  report.complete=report.page_languages.length===all.length&&Object.values(report.checks).every(x=>x===true);
  if (!report.complete) throw Error('Offline interaction checks incomplete or failed.');
})().catch(error=>{report.error=String(error);process.exitCode=2;}).finally(async()=>{
  if(browser)await browser.close();report.finished_at=new Date().toISOString();
  // Bind to exactly the bytes still present after execution.
  if(hash(fs.readFileSync(path.join(site,'build-manifest.json')))!==report.manifest_sha256){report.complete=false;report.error='Manifest changed during browser verification.';process.exitCode=2;}
  fs.writeFileSync(path.join(out,'browser-report.json'),JSON.stringify(report,null,2));
  console.log(JSON.stringify({complete:report.complete,page_languages:report.page_languages.length,checks:report.checks,error:report.error||null}));
});
