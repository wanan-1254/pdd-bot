(function(){
var fs=require('fs'),vm=require('vm');
var bg={
  console:console,setTimeout:setTimeout,setInterval:setInterval,clearTimeout:clearTimeout,clearInterval:clearInterval,
  Date:Date,Promise:Promise,Array:Array,Object:Object,String:String,Number:Number,Boolean:Boolean,Math:Math,
  JSON:JSON,RegExp:RegExp,Error:Error,TypeError:TypeError,parseInt:parseInt,parseFloat:parseFloat,
  isNaN:isNaN,isFinite:isFinite,undefined:undefined,NaN:NaN,Infinity:Infinity,
  encodeURI:encodeURI,decodeURI:decodeURI,encodeURIComponent:encodeURIComponent,decodeURIComponent:decodeURIComponent,
  escape:escape,unescape:unescape,
  Uint8Array:Uint8Array,Uint16Array:Uint16Array,Int32Array:Int32Array,Uint32Array:Uint32Array,
  Int8Array:Int8Array,Int16Array:Int16Array,Float32Array:Float32Array,Float64Array:Float64Array,
  ArrayBuffer:ArrayBuffer,DataView:DataView,Map:Map,Set:Set,WeakMap:WeakMap,WeakSet:WeakSet,
  Symbol:Symbol,Proxy:Proxy,Reflect:Reflect,
  atob:function(s){return Buffer.from(s,'base64').toString('binary')},
  btoa:function(s){return Buffer.from(s,'binary').toString('base64')},
  Element:function(){},HTMLElement:function(){},Node:function(){},Event:function(){},
  MutationObserver:function(){this.observe=function(){}},
  getComputedStyle:function(){return{getPropertyValue:function(){return''}}},
  requestAnimationFrame:function(cb){return setTimeout(cb,16)},
  cancelAnimationFrame:function(id){clearTimeout(id)},
  screen:{width:1920,height:1080,availWidth:1920,availHeight:1040,colorDepth:24,pixelDepth:24},
  document:{
    createElement:function(){return{tagName:'DIV',style:{},setAttribute:function(){},getAttribute:function(){return null},appendChild:function(){},addEventListener:function(){},getElementsByTagName:function(){return[]},querySelector:function(){return null},querySelectorAll:function(){return[]},innerHTML:'',textContent:'',childNodes:[],children:[],nodeType:1}},
    createTextNode:function(){return{nodeType:3}},createDocumentFragment:function(){return{appendChild:function(){}}},
    getElementById:function(){return null},getElementsByTagName:function(){return[]},querySelector:function(){return null},querySelectorAll:function(){return[]},
    cookie:'',title:'',domain:'mobile.yangkeduo.com',URL:'https://mobile.yangkeduo.com/charge_sign_coupon.html',
    body:{appendChild:function(){},style:{},nodeType:1},head:{appendChild:function(){}},
    documentElement:{style:{},getAttribute:function(){return null},nodeType:1},
    addEventListener:function(){},removeEventListener:function(){},readyState:'complete',nodeType:9
  },
  location:{href:'https://mobile.yangkeduo.com/charge_sign_coupon.html',hostname:'mobile.yangkeduo.com',host:'mobile.yangkeduo.com',protocol:'https:',pathname:'/charge_sign_coupon.html',search:'',hash:'',origin:'https://mobile.yangkeduo.com'},
  navigator:{userAgent:'Mozilla/5.0 (Linux; Android 13; Pixel 7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Mobile Safari/537.36',platform:'Linux armv8l',language:'zh-CN',languages:['zh-CN','zh','en'],cookieEnabled:true,onLine:true,hardwareConcurrency:8,maxTouchPoints:5,vendor:'Google Inc.'},
  history:{length:1,pushState:function(){},replaceState:function(){}},
  performance:{now:function(){return Date.now()},timing:{navigationStart:Date.now()-5000},getEntriesByType:function(){return[]}},
  localStorage:{_d:{},getItem:function(k){return this._d[k]||null},setItem:function(k,v){this._d[k]=String(v)},removeItem:function(k){delete this._d[k]}},
  sessionStorage:{_d:{},getItem:function(k){return this._d[k]||null},setItem:function(k,v){this._d[k]=String(v)},removeItem:function(k){delete this._d[k]}},
  crypto:{getRandomValues:function(a){for(var i=0;i<a.length;i++)a[i]=Math.floor(Math.random()*256);return a}},
  fetch:function(){return Promise.resolve({json:function(){return Promise.resolve({})}})},
  XMLHttpRequest:function(){this.open=function(){};this.send=function(){};this.setRequestHeader=function(){};this.addEventListener=function(){};this.removeEventListener=function(){}},
  Image:function(){this.src=''},
  __LOADABLE_LOADED_CHUNKS__:[]
};
bg.self=bg;bg.window=bg;bg.globalThis=bg;
var src=fs.readFileSync('risk-control-anti.js','utf-8');
var ctx=vm.createContext(bg);
try{vm.runInContext(src,ctx)}catch(e){console.error('Eval:',e.message)}
var chunks=bg.__LOADABLE_LOADED_CHUNKS__;
if(!chunks||!chunks.length){console.log('No chunks');process.exit(1)}
var mods=null;
for(var i=0;i<chunks.length;i++){if(chunks[i]&&chunks[i][1]&&chunks[i][1][32455]){mods=chunks[i][1];break}}
if(!mods){console.log('Module 32455 not found');process.exit(1)}
console.log('Found module 32455');
var mock={exports:{}};
mods[32455].call(mock.exports,mock);
var AC=mock.exports.default||mock.exports;
if(typeof AC!=='function'){console.log('Not a constructor:',typeof AC);process.exit(1)}
console.log('Proto:',Object.getOwnPropertyNames(AC.prototype||{}));
(async function(){
  var results=[];
  for(var i=0;i<5;i++){
    var st=Date.now()+i*1000;
    try{
      var inst=new AC({serverTime:st});
      console.log('Instance keys:',Object.keys(inst));
      console.log('Proto keys:',Object.getOwnPropertyNames(Object.getPrototypeOf(inst)));
      var token=await inst.messagePack();
      results.push({st:st,token:token,len:token?token.length:0,pre:token?token.substring(0,20):''});
    }catch(e){results.push({st:st,error:e.message,stack:e.stack?e.stack.substring(0,300):''})}
  }
  console.log(JSON.stringify(results,null,2));
})();
})();
