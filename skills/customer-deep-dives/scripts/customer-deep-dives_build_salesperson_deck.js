// Build a single salesperson's deck (cover + side sections + customer slides)
// Usage: node build_sp_deck.js <salesperson_json_filename>
// Example: node build_sp_deck.js Caroline_McIntosh.json
const pptxgen = require("pptxgenjs");
const fs = require("fs");
const path = require("path");

const inputFile = process.argv[2];
if (!inputFile) {
  console.error("Usage: node build_sp_deck.js <salesperson_json>");
  process.exit(1);
}

const deck = JSON.parse(fs.readFileSync(`/home/claude/sp_decks/${inputFile}`, 'utf8'));
const SP_NAME = deck.salesperson;
const SAFE = inputFile.replace('.json', '');

const pres = new pptxgen();
pres.layout = "LAYOUT_WIDE";
pres.title = `P+P ${SP_NAME} — Customer Sales & Budget Analysis 2026`;

// Color tokens
const C = {
  navy:"0B1E3A", white:"FFFFFF", light:"E2E8F0",
  muted:"8896B0", slate:"475569", text:"1E293B",
  red:"DC2626", darkred:"991B1B", lred:"FEF2F2",
  amber:"D97706", lamber:"FFFBEB",
  green:"15803D", lgreen:"F0FDF4",
  teal:"0D7490", lteal:"EFF6FF",
  ff: "7C3AED",  // Fetch purple
  bb: "EA580C",  // Brand Buzz orange
};

const TBL_Y = 1.00, TBL_X = 3.20, TBL_W = 9.85;
const TBL_HDR_H = 0.28, TBL_ROW_H = 0.15, TBL_TOT_H = 0.20;
const PANEL_X = 0.30, PANEL_W = 2.80;
const CALLOUT_LY = 2.93, CALLOUT_CY = 3.15, CARD_H = 3.04;
const BUDGET_Y = 6.25;
const YTD_FRAC = 0.30, H1_FRAC = 0.45;
const CW = [2.0,1.2,1.35,1.1,0.9,0.95,1.2,1.15];

const fmtK = v => { const a=Math.abs(v); return a>=1e6?'$'+(a/1e6).toFixed(a>=10e6?1:2)+'M':'$'+Math.round(a/1000)+'K'; };
const signFmt = v => (v>=0?'+':'−')+fmtK(v);

function mkComment(r, isLoss) {
  const cov  = Math.round(r.cov)+'%';
  const est  = fmtK(r.est);
  const bdgt = fmtK(r.bdgt);
  const ly   = fmtK(r.fy25);
  const gap  = fmtK(Math.abs(r.miss));
  const lyPace = r.fy25>0 ? Math.round((r.ytd/(r.fy25*YTD_FRAC)-1)*100) : 0;
  const pace = (lyPace>=0?'+':'')+lyPace+'% vs LY';
  if (isLoss) {
    const oo = r.oo>0 ? fmtK(r.oo)+' OO placed. ' : 'No open orders. ';
    return 'Budget: '+bdgt+'  |  Est. Sales: '+est+'  |  LY: '+ly+'\n'+cov+' of budget covered (YTD+OO). '+oo+'Running '+pace+'. H2 programs needed to close '+gap+' gap.';
  } else {
    return 'Budget: '+bdgt+'  |  Est. Sales: '+est+'  |  LY: '+ly+'\n'+cov+' of budget confirmed. Running '+pace+'. On pace to beat budget by '+gap+'.';
  }
}

const hF = {color:C.navy};
const hdr = (txt,a='right') => ({text:txt,options:{bold:true,color:C.white,fill:hF,fontSize:7,fontFace:'Calibri',align:a,margin:2}});
const cell = (txt,a='right',col=C.text,bg=C.white,fs=7.5,bold=false) => ({
  text:txt,options:{fill:{color:bg},fontSize:fs,fontFace:'Calibri',color:col,align:a,bold,margin:1,valign:'middle'}
});
const RISK_COL = {CRITICAL:C.darkred,HIGH:C.red,MEDIUM:C.amber,WATCH:C.teal,'ON TRACK':C.green,'NO BUDGET':'94A3B8'};

// =============================================================================
// COVER SLIDE — salesperson-specific
// =============================================================================
function buildCover() {
  const s = pres.addSlide();
  s.background = {color: C.navy};
  
  // Header strip
  s.addText('Pets+People  ·  Salesperson Review',{
    x:0.6, y:0.7, w:11, h:0.4, fontSize:11, color:'6B9EFF',
    fontFace:'Calibri', bold:true, charSpacing:4, margin:0
  });
  
  // Determine accent color: if FF-only → purple, BB-only → orange, dual → navy/white
  const ffSection = deck.sections.FF;
  const bbSection = deck.sections.BB;
  const isDual = !!ffSection && !!bbSection;
  const accent = isDual ? '6B9EFF' : (ffSection ? C.ff : C.bb);
  
  // Big salesperson name
  s.addShape(pres.shapes.RECTANGLE,{
    x:0.6, y:1.3, w:0.16, h:1.3, fill:{color:accent}, line:{color:accent,width:0}
  });
  s.addText(SP_NAME,{
    x:0.85, y:1.20, w:9, h:1.4, fontSize:54, bold:true,
    color:accent, fontFace:'Georgia', margin:0, wrap:true
  });
  
  // Tagline based on coverage
  let tagline;
  if (isDual) tagline = 'Pet & People Combined Territory';
  else if (ffSection) tagline = 'Pet Products Territory';
  else tagline = 'People Products Territory';
  s.addText(tagline,{
    x:0.85, y:2.65, w:9, h:0.4, fontSize:14, color:'A5C0FF',
    fontFace:'Calibri', italic:true, charSpacing:2, margin:0
  });
  
  // Subtitle
  s.addText('Customer Sales & Budget Analysis',{
    x:0.6, y:3.4, w:9, h:0.55, fontSize:24, bold:true,
    color:C.white, fontFace:'Georgia', margin:0
  });
  s.addText('FY 2025 Actual  vs  FY 2026 Budget  vs  2026 Estimated Sales',{
    x:0.6, y:3.95, w:9, h:0.42, fontSize:12, color:'A5C0FF',
    fontFace:'Calibri', italic:true, margin:0
  });
  
  // Customer count line
  const totalCusts = Object.values(deck.sections).reduce((s,sec)=>s+sec.cust_count,0);
  const sectionLabel = isDual ? 'across Fetch & Brand Buzz' :
    (ffSection ? 'Fetch (Pet)' : 'Brand Buzz (People)');
  s.addText(`${totalCusts} customer accounts ≥$100K  ·  ${sectionLabel}`,{
    x:0.6, y:4.40, w:9, h:0.30, fontSize:10, color:'5570A0',
    fontFace:'Calibri', italic:true, margin:0
  });
  
  // Right column: portfolio metric boxes — combined or per side
  // Combined portfolio at top
  const combined = {fy25:0, bdgt:0, ytd:0, oo:0, est:0};
  Object.values(deck.sections).forEach(sec => {
    combined.fy25 += sec.totals.fy25;
    combined.bdgt += sec.totals.bdgt;
    combined.ytd  += sec.totals.ytd;
    combined.oo   += sec.totals.oo;
    combined.est  += sec.totals.est;
  });
  combined.miss = combined.est - combined.bdgt;
  
  const boxes = [
    {l:'FY 2025 Actual', v:fmtK(combined.fy25), sub:'Full year invoiced', col:C.white},
    {l:'FY 2026 Budget', v:fmtK(combined.bdgt), 
      sub: combined.fy25>0 ? ((combined.bdgt/combined.fy25-1)*100>=0?'+':'')+((combined.bdgt/combined.fy25-1)*100).toFixed(1)+'% vs 2025' : '—', 
      col:C.white},
    {l:'2026 Estimated Sales', v:fmtK(combined.est), sub:'MAX(YTD÷30%, H1÷45%)', col:C.amber},
    {l:'Potential Budget Miss', v:signFmt(combined.miss), 
      sub: combined.miss>=0?'On pace':'vs full year plan', 
      col:combined.miss>=0?C.green:C.red},
  ];
  boxes.forEach((k,i) => {
    const ky = 1.2 + i*1.5;
    s.addShape(pres.shapes.RECTANGLE,{
      x:9.5, y:ky, w:3.6, h:1.35,
      fill:{color:'0D2545'}, line:{color: k.col===C.white?'1E4080':k.col, width:1}
    });
    s.addText(k.v,{x:9.55, y:ky+0.10, w:3.5, h:0.62, fontSize:22, bold:true, color:k.col, fontFace:'Georgia', align:'center', margin:0});
    s.addText(k.l,{x:9.55, y:ky+0.78, w:3.5, h:0.26, fontSize:10, color:'6B9EFF', fontFace:'Calibri', align:'center', margin:0});
    s.addText(k.sub,{x:9.55, y:ky+1.06, w:3.5, h:0.20, fontSize:8, color:'5570A0', fontFace:'Calibri', align:'center', margin:0});
  });
  
  // Footer
  s.addText('Generated May 2026  ·  Internal Use Only  ·  Pets+People Sales Review',{
    x:0.6, y:7.1, w:12, h:0.22, fontSize:8.5, color:'5570A0', fontFace:'Calibri', margin:0
  });
}

// =============================================================================
// SECTION DIVIDER
// =============================================================================
function addSectionDivider(sideName, sub, accent) {
  const s = pres.addSlide();
  s.background = {color: C.navy};
  s.addShape(pres.shapes.RECTANGLE,{x:0.4, y:2.6, w:0.07, h:2.0, fill:{color:accent}, line:{color:accent,width:0}});
  s.addText(sideName,{x:0.62, y:2.55, w:12, h:1.1, fontSize:48, bold:true, color:C.white, fontFace:'Georgia', margin:0, wrap:true});
  if (sub) s.addText(sub,{x:0.62, y:3.85, w:12, h:0.5, fontSize:14, color:accent, fontFace:'Calibri', italic:true, margin:0});
  
  // Salesperson name on the divider so context is clear
  s.addText(SP_NAME,{x:0.62, y:4.45, w:12, h:0.4, fontSize:13, color:'A5C0FF', fontFace:'Calibri', italic:true, margin:0});
}

// =============================================================================
// CUSTOMER DEEP-DIVE SLIDE  (mostly carried over from prior split-deck builder)
// =============================================================================
function buildCustomerSlide(cust, accent, sideName, pgLabel) {
  const t = cust.totals;
  const rows = cust.brand_rows || [];
  const riskColor = RISK_COL[cust.risk] || C.red;
  const isGain = t.miss >= 0;
  const panelBg = isGain ? C.lgreen : C.lred;
  const panelBorder = isGain ? C.green : C.red;

  const topLosses = rows.filter(r => r.miss < 0).slice(0, 3);
  const topGains = rows.filter(r => r.miss > 0).sort((a,b) => b.miss-a.miss).slice(0,3);

  const s = pres.addSlide();
  s.background = {color: C.white};

  // Header
  s.addShape(pres.shapes.RECTANGLE,{x:0,y:0,w:13.33,h:0.88,fill:{color:C.navy},line:{color:C.navy,width:0}});
  s.addShape(pres.shapes.RECTANGLE,{x:0,y:0,w:0.28,h:0.88,fill:{color:riskColor},line:{color:riskColor,width:0}});
  const titleStr = cust.name + (pgLabel ? '  ('+pgLabel+')' : '');
  s.addText(titleStr,{x:0.42,y:0.06,w:4.8,h:0.44,fontSize:20,bold:true,color:C.white,fontFace:'Georgia',margin:0,wrap:false});
  // Side label tag
  s.addText(sideName,{x:0.42,y:0.50,w:4.8,h:0.20,fontSize:9,bold:true,color:accent,fontFace:'Calibri',charSpacing:3,margin:0});

  s.addShape(pres.shapes.RECTANGLE,{x:5.5,y:0.10,w:4.1,h:0.68,fill:{color:'0D2545'},line:{color:'1E4080',width:0.5}});
  s.addText('LY 2025',{x:5.55,y:0.12,w:1.85,h:0.20,fontSize:8,color:'6B9EFF',fontFace:'Calibri',align:'center',margin:0,bold:true});
  s.addText(fmtK(t.fy25),{x:5.55,y:0.32,w:1.85,h:0.38,fontSize:16,bold:true,color:C.white,fontFace:'Georgia',align:'center',margin:0});
  s.addShape(pres.shapes.LINE,{x:7.38,y:0.16,w:0,h:0.56,line:{color:'1E4080',width:0.5}});
  s.addText('FY 2026 Budget',{x:7.43,y:0.12,w:2.10,h:0.20,fontSize:8,color:'6B9EFF',fontFace:'Calibri',align:'center',margin:0,bold:true});
  s.addText(t.bdgt>0?fmtK(t.bdgt):'No Budget',{x:7.43,y:0.32,w:2.10,h:0.38,fontSize:16,bold:true,color:'A5C0FF',fontFace:'Georgia',align:'center',margin:0});
  if(t.bdgt>0 && t.fy25>0){
    const lyD = (t.bdgt/t.fy25-1)*100;
    s.addText('Budget '+(lyD>=0?'+':'')+lyD.toFixed(1)+'% vs LY',{x:9.70,y:0.28,w:1.80,h:0.30,fontSize:9.5,color:'94A3B8',fontFace:'Calibri',align:'center',margin:0,italic:true});
  }
  s.addShape(pres.shapes.RECTANGLE,{x:11.65,y:0.20,w:1.55,h:0.48,fill:{color:riskColor},line:{color:riskColor,width:0}});
  s.addText(cust.risk,{x:11.65,y:0.20,w:1.55,h:0.48,fontSize:9.5,bold:true,color:C.white,fontFace:'Calibri',align:'center',margin:0});

  // Left panel
  s.addShape(pres.shapes.RECTANGLE,{x:PANEL_X,y:TBL_Y,w:PANEL_W,h:5.10,fill:{color:panelBg},line:{color:panelBorder,width:1.5}});
  s.addText(signFmt(t.miss),{x:PANEL_X+0.1,y:TBL_Y+0.08,w:PANEL_W-0.2,h:0.50,fontSize:26,bold:true,color:isGain?C.green:C.red,fontFace:'Georgia',align:'center',margin:0});
  s.addText('Potential Budget Miss',{x:PANEL_X+0.1,y:TBL_Y+0.60,w:PANEL_W-0.2,h:0.18,fontSize:7.5,color:C.muted,fontFace:'Calibri',align:'center',margin:0});
  s.addShape(pres.shapes.LINE,{x:PANEL_X+0.15,y:TBL_Y+0.84,w:PANEL_W-0.3,h:0,line:{color:C.light,width:0.5}});
  const covColor = t.cov<25?C.red:t.cov<40?C.amber:C.green;
  s.addText(Math.round(t.cov)+'%',{x:PANEL_X+0.1,y:TBL_Y+0.92,w:PANEL_W-0.2,h:0.40,fontSize:22,bold:true,color:covColor,fontFace:'Georgia',align:'center',margin:0});
  s.addText('YTD + OO vs Budget',{x:PANEL_X+0.1,y:TBL_Y+1.34,w:PANEL_W-0.2,h:0.16,fontSize:7,color:C.muted,fontFace:'Calibri',align:'center',margin:0});
  const bX=PANEL_X+0.20,bY=TBL_Y+1.54;
  s.addShape(pres.shapes.RECTANGLE,{x:bX,y:bY,w:PANEL_W-0.4,h:0.09,fill:{color:'E2E8F0'},line:{color:'E2E8F0',width:0}});
  if(t.bdgt>0) s.addShape(pres.shapes.RECTANGLE,{x:bX,y:bY,w:(PANEL_W-0.4)*Math.min(t.cov/100,1),h:0.09,fill:{color:covColor},line:{color:covColor,width:0}});
  s.addText(fmtK(t.ytd)+' shipped + '+fmtK(t.oo)+' OO = '+fmtK(t.ytd+t.oo)+(t.bdgt>0?' of '+fmtK(t.bdgt):''),{x:PANEL_X+0.1,y:bY+0.12,w:PANEL_W-0.2,h:0.15,fontSize:6.5,color:C.muted,fontFace:'Calibri',align:'center',margin:0});
  s.addShape(pres.shapes.LINE,{x:PANEL_X+0.15,y:TBL_Y+1.96,w:PANEL_W-0.3,h:0,line:{color:C.light,width:0.5}});

  s.addShape(pres.shapes.RECTANGLE,{x:PANEL_X+0.1,y:TBL_Y+2.04,w:PANEL_W-0.2,h:0.68,fill:{color:'FFF7ED'},line:{color:'FED7AA',width:0.5}});
  s.addText('Estimation Method',{x:PANEL_X+0.15,y:TBL_Y+2.08,w:PANEL_W-0.3,h:0.18,fontSize:7.5,bold:true,color:C.amber,fontFace:'Calibri',margin:0});
  s.addText('Est = MAX(YTD÷30%, (YTD+OO)÷45%)\nOO assumed to ship in H1 (Q2)\nH2 projected at 55/45 ratio',{x:PANEL_X+0.15,y:TBL_Y+2.28,w:PANEL_W-0.3,h:0.42,fontSize:7,color:C.text,fontFace:'Calibri',margin:0,wrap:true});
  s.addShape(pres.shapes.LINE,{x:PANEL_X+0.15,y:TBL_Y+2.78,w:PANEL_W-0.3,h:0,line:{color:C.light,width:0.5}});

  [
    {l:'LY 2025 Actual',v:fmtK(t.fy25),col:C.slate},
    {l:'FY 2026 Budget (BV0326)',v:t.bdgt>0?fmtK(t.bdgt):'Not Budgeted',col:'0D4E6B'},
    {l:'YTD 2026 Shipped',v:fmtK(t.ytd),col:C.text},
    {l:'Open Orders (Pipeline)',v:t.oo>0?fmtK(t.oo):'—',col:t.oo>0?C.green:C.muted},
    {l:'2026 Estimated Sales',v:fmtK(t.est),col:isGain?C.green:C.red},
  ].forEach((st,i)=>{
    const sy=TBL_Y+2.86+i*0.43;
    s.addShape(pres.shapes.RECTANGLE,{x:PANEL_X+0.1,y:sy,w:PANEL_W-0.2,h:0.38,fill:{color:C.white},line:{color:C.light,width:0.4}});
    s.addText(st.v,{x:PANEL_X+0.1,y:sy+0.04,w:PANEL_W-0.2,h:0.20,fontSize:11,bold:true,color:st.col,fontFace:'Georgia',align:'center',margin:0});
    s.addText(st.l,{x:PANEL_X+0.1,y:sy+0.25,w:PANEL_W-0.2,h:0.11,fontSize:6,color:C.muted,fontFace:'Calibri',align:'center',margin:0});
  });

  // Brand table
  const tableRows = [
    [hdr('BRAND','left'),hdr('LY 2025\nActual'),hdr('FY 2026\nBudget'),hdr('YTD 2026\nShipped'),hdr('Open\nOrders'),hdr('YTD+OO\n% Budget'),hdr('2026\nEst. Sales'),hdr('Potential\nBudget Miss')],
    ...rows.map(r=>{
      const bg=r.miss<-300000?'FFF1F2':r.miss<-30000?'FFF8F0':r.miss>0?'F0FDF4':C.white;
      const mCol=r.miss<0?C.red:C.green;
      const cCol=r.cov<25?C.red:r.cov<35?C.amber:C.green;
      const brandLabel = r.disc ? (r.brand + '  *DISC*') : r.brand;
      return [
        cell(brandLabel,'left',C.text,bg,7.5,true),
        cell(fmtK(r.fy25),'right',C.slate,bg,7.5),
        cell(r.bdgt>0?fmtK(r.bdgt):'—','right','0D4E6B',bg,7.5),
        cell(fmtK(r.ytd),'right',C.text,bg,7.5),
        cell(r.oo>0?fmtK(r.oo):'—','right',r.oo>0?C.green:C.muted,bg,7.5),
        cell(r.bdgt>0?Math.round(r.cov)+'%':'—','right',cCol,bg,7.5,r.bdgt>0),
        cell(fmtK(r.est),'right',C.teal,bg,7.5),
        cell(r.bdgt>0?signFmt(r.miss):signFmt(r.est-r.fy25),'right',mCol,bg,8,true),
      ];
    }),
    [
      cell('TOTAL — '+cust.name.toUpperCase().slice(0,22),'left','0D4E6B','EFF6FF',7.5,true),
      cell(fmtK(t.fy25),'right','0D4E6B','EFF6FF',8,true),
      cell(t.bdgt>0?fmtK(t.bdgt):'—','right','0D4E6B','EFF6FF',8,true),
      cell(fmtK(t.ytd),'right','0D4E6B','EFF6FF',8,true),
      cell(t.oo>0?fmtK(t.oo):'—','right',C.green,'EFF6FF',8,true),
      cell(t.bdgt>0?Math.round(t.cov)+'%':'—','right',covColor,'EFF6FF',8,true),
      cell(fmtK(t.est),'right',C.teal,'EFF6FF',8,true),
      cell(signFmt(t.miss),'right',isGain?C.green:C.red,'EFF6FF',8.5,true),
    ]
  ];
  s.addTable(tableRows,{x:TBL_X,y:TBL_Y,w:TBL_W,colW:CW,
    rowH:[TBL_HDR_H,...rows.map(()=>TBL_ROW_H),TBL_TOT_H],border:{pt:0.4,color:'E2E8F0'}});

  // 3-section callout
  const LOSS_W = 4.10, GAIN_W = 1.55, PROG_W = 1.75;
  const GAIN_LX = TBL_X + LOSS_W + 0.05;
  const PROG_LX = GAIN_LX + GAIN_W + 0.05;

  if(topLosses.length>0){
    s.addShape(pres.shapes.RECTANGLE,{x:TBL_X,y:CALLOUT_LY,w:LOSS_W,h:0.18,fill:{color:C.darkred},line:{color:C.darkred,width:0}});
    s.addText('▼  TOP POTENTIAL BUDGET MISSES',{x:TBL_X+0.10,y:CALLOUT_LY+0.02,w:LOSS_W-0.14,h:0.14,fontSize:8,bold:true,color:C.white,fontFace:'Calibri',margin:0});
  }
  if(topGains.length>0){
    s.addShape(pres.shapes.RECTANGLE,{x:GAIN_LX,y:CALLOUT_LY,w:GAIN_W,h:0.18,fill:{color:'134C26'},line:{color:'134C26',width:0}});
    s.addText('▲  AHEAD OF BUDGET',{x:GAIN_LX+0.10,y:CALLOUT_LY+0.02,w:GAIN_W-0.14,h:0.14,fontSize:8,bold:true,color:C.white,fontFace:'Calibri',margin:0});
  }
  const progs = cust.programs || {won:[],lost:[]};
  if(progs.won.length>0 || progs.lost.length>0){
    s.addShape(pres.shapes.RECTANGLE,{x:PROG_LX,y:CALLOUT_LY,w:PROG_W,h:0.18,fill:{color:'1E3A8A'},line:{color:'1E3A8A',width:0}});
    s.addText('★  BRAND ENTRIES / EXITS',{x:PROG_LX+0.10,y:CALLOUT_LY+0.02,w:PROG_W-0.14,h:0.14,fontSize:8,bold:true,color:C.white,fontFace:'Calibri',margin:0});
  }

  const n_loss = topLosses.length;
  if(n_loss>0){
    const lcw = (LOSS_W-0.06*(n_loss-1))/n_loss;
    topLosses.forEach((r,i)=>{
      const cx=TBL_X+i*(lcw+0.06),cy=CALLOUT_CY;
      s.addShape(pres.shapes.RECTANGLE,{x:cx,y:cy,w:lcw,h:CARD_H,fill:{color:'FEF2F2'},line:{color:C.red,width:0.8}});
      s.addShape(pres.shapes.RECTANGLE,{x:cx,y:cy,w:lcw,h:0.05,fill:{color:C.red},line:{color:C.red,width:0}});
      s.addText(r.brand,{x:cx+0.10,y:cy+0.09,w:lcw-0.18,h:0.32,fontSize:8.5,bold:true,color:C.text,fontFace:'Georgia',margin:0,wrap:true,valign:'top'});
      s.addText(signFmt(r.miss),{x:cx+0.10,y:cy+0.42,w:lcw-0.18,h:0.18,fontSize:11,bold:true,color:C.red,fontFace:'Georgia',margin:0,wrap:false});
      s.addShape(pres.shapes.LINE,{x:cx+0.10,y:cy+0.62,w:lcw-0.18,h:0,line:{color:C.light,width:0.5}});
      s.addText(mkComment(r,true),{x:cx+0.10,y:cy+0.67,w:lcw-0.16,h:CARD_H-0.73,fontSize:6.5,color:C.text,fontFace:'Calibri',margin:0,wrap:true,paraSpaceAfter:2});
    });
  }
  const n_gain = topGains.length;
  if(n_gain>0){
    const r = topGains[0];
    const cx = GAIN_LX, cy = CALLOUT_CY;
    s.addShape(pres.shapes.RECTANGLE,{x:cx,y:cy,w:GAIN_W,h:CARD_H,fill:{color:'F0FDF4'},line:{color:C.green,width:0.8}});
    s.addShape(pres.shapes.RECTANGLE,{x:cx,y:cy,w:GAIN_W,h:0.05,fill:{color:C.green},line:{color:C.green,width:0}});
    s.addText(r.brand,{x:cx+0.10,y:cy+0.09,w:GAIN_W-0.18,h:0.32,fontSize:8,bold:true,color:C.text,fontFace:'Georgia',margin:0,wrap:true,valign:'top'});
    s.addText(signFmt(r.miss),{x:cx+0.10,y:cy+0.42,w:GAIN_W-0.18,h:0.18,fontSize:11,bold:true,color:C.green,fontFace:'Georgia',margin:0,wrap:false});
    s.addShape(pres.shapes.LINE,{x:cx+0.10,y:cy+0.62,w:GAIN_W-0.18,h:0,line:{color:C.light,width:0.5}});
    s.addText(mkComment(r,false),{x:cx+0.10,y:cy+0.67,w:GAIN_W-0.16,h:CARD_H-0.73,fontSize:6.5,color:C.text,fontFace:'Calibri',margin:0,wrap:true,paraSpaceAfter:2});
  }
  if(progs.won.length>0 || progs.lost.length>0){
    const cx = PROG_LX, cy = CALLOUT_CY;
    s.addShape(pres.shapes.RECTANGLE,{x:cx,y:cy,w:PROG_W,h:CARD_H,fill:{color:'F1F5F9'},line:{color:'1E3A8A',width:0.8}});
    s.addShape(pres.shapes.RECTANGLE,{x:cx,y:cy,w:PROG_W,h:0.05,fill:{color:'1E3A8A'},line:{color:'1E3A8A',width:0}});
    const netWon = progs.won.reduce((s,p)=>s+p.amt,0);
    const netLost = progs.lost.reduce((s,p)=>s+p.amt,0);
    const netImpact = netWon - netLost;
    const netCol = netImpact>=0 ? C.green : C.red;
    const netSign = netImpact>=0 ? '+' : '−';
    s.addText(`Net Brands: ${netSign}${fmtK(Math.abs(netImpact))}`,{x:cx+0.08,y:cy+0.10,w:PROG_W-0.16,h:0.16,fontSize:9,bold:true,color:netCol,fontFace:'Georgia',margin:0,wrap:false});
    s.addText(`Won: +${fmtK(netWon)}  ·  Lost: −${fmtK(netLost)}`,{x:cx+0.08,y:cy+0.27,w:PROG_W-0.16,h:0.13,fontSize:6.5,color:C.muted,fontFace:'Calibri',margin:0,wrap:false});
    s.addShape(pres.shapes.LINE,{x:cx+0.08,y:cy+0.44,w:PROG_W-0.16,h:0,line:{color:C.light,width:0.5}});
    const items = [];
    progs.won.forEach(p => items.push({type:'WON',brand:p.brand,cat:p.cat,amt:p.amt}));
    progs.lost.forEach(p => items.push({type:'LOST',brand:p.brand,cat:p.cat,amt:-p.amt}));
    items.sort((a,b) => Math.abs(b.amt) - Math.abs(a.amt));
    const ROW_Y = cy + 0.50, ROW_H = 0.27;
    const MAX_ROWS = Math.floor((CARD_H - 0.55) / ROW_H);
    items.slice(0, MAX_ROWS).forEach((p,i) => {
      const ry = ROW_Y + i*ROW_H;
      const isWon = p.type === 'WON';
      const dotCol = isWon ? C.green : C.red;
      s.addShape(pres.shapes.OVAL,{x:cx+0.10,y:ry+0.04,w:0.08,h:0.08,fill:{color:dotCol},line:{color:dotCol,width:0}});
      s.addText(p.brand,{x:cx+0.22,y:ry,w:PROG_W-0.85,h:0.14,fontSize:7,bold:true,color:C.text,fontFace:'Calibri',margin:0,wrap:false});
      const amtStr = (p.amt>=0?'+':'−')+fmtK(Math.abs(p.amt));
      s.addText(amtStr,{x:cx+PROG_W-0.62,y:ry,w:0.55,h:0.14,fontSize:7.5,bold:true,color:dotCol,fontFace:'Calibri',align:'right',margin:0,wrap:false});
      s.addText(p.cat,{x:cx+0.22,y:ry+0.13,w:PROG_W-0.30,h:0.12,fontSize:6,color:C.muted,fontFace:'Calibri',margin:0,wrap:false});
    });
  }

  // Budget bar removed May 2026 per Steven — same data already shown in left panel + table TOTAL row
}

// =============================================================================
// MAIN
// =============================================================================
buildCover();

// Order: FF first then BB (consistent across all decks)
const sectionOrder = ['FF', 'BB'].filter(d => deck.sections[d]);
sectionOrder.forEach(div => {
  const sec = deck.sections[div];
  const accent = sec.accent;
  const sideName = sec.side_name;
  
  addSectionDivider(
    sideName,
    `${sec.cust_count} customer accounts  ·  ${sec.tagline}  ·  sorted by Potential Budget Miss (worst first)`,
    accent
  );
  
  sec.customers.forEach(cust => {
    const rows = cust.brand_rows || [];
    if (rows.length <= 9) {
      buildCustomerSlide(cust, accent, sideName, null);
    } else {
      buildCustomerSlide({...cust, brand_rows: rows.slice(0,9)}, accent, sideName, '1 of 2');
      buildCustomerSlide({...cust, brand_rows: rows.slice(9,18)}, accent, sideName, '2 of 2');
    }
  });
});

const outPath = `/mnt/user-data/outputs/PP_${SAFE}_Deck.pptx`;
pres.writeFile({fileName: outPath})
  .then(() => console.log(`✅ ${SP_NAME}: ${pres.slides.length} slides → ${outPath}`))
  .catch(e => {console.error('❌', e.message); process.exit(1);});
