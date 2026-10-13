// El recuadro para firmar con el dedo (roles de pago y constancia). Uno por página:
// #pad (contenedor), #lienzo (canvas), #borrar, #firmar, #form-firma y el input #firma.
(function(){
  var pad=document.getElementById('pad'), c=document.getElementById('lienzo'), x=c.getContext('2d'),
      btn=document.getElementById('firmar'), largo=0, ultimo=null;
  function medir(){
    var r=c.getBoundingClientRect(), d=window.devicePixelRatio||1;
    c.width=Math.round(r.width*d); c.height=Math.round(r.height*d);
    x.setTransform(d,0,0,d,0,0); x.lineWidth=2.6; x.lineCap='round'; x.lineJoin='round'; x.strokeStyle='#0f172a';
    largo=0; btn.disabled=true; pad.classList.remove('firmando');
  }
  function punto(e){var r=c.getBoundingClientRect();return {x:e.clientX-r.left,y:e.clientY-r.top};}
  c.addEventListener('pointerdown',function(e){e.preventDefault();c.setPointerCapture(e.pointerId);ultimo=punto(e);pad.classList.add('firmando');
    x.beginPath();x.arc(ultimo.x,ultimo.y,1.2,0,7);x.fillStyle='#0f172a';x.fill();});
  c.addEventListener('pointermove',function(e){if(!ultimo)return;e.preventDefault();var p=punto(e);
    x.beginPath();x.moveTo(ultimo.x,ultimo.y);x.lineTo(p.x,p.y);x.stroke();
    largo+=Math.hypot(p.x-ultimo.x,p.y-ultimo.y);ultimo=p;if(largo>60)btn.disabled=false;});
  ['pointerup','pointercancel','pointerleave'].forEach(function(n){c.addEventListener(n,function(){ultimo=null;});});
  document.getElementById('borrar').addEventListener('click',function(){x.clearRect(0,0,c.width,c.height);medir();});
  document.getElementById('form-firma').addEventListener('submit',function(e){
    if(largo<=60){e.preventDefault();return;}
    document.getElementById('firma').value=c.toDataURL('image/png'); btn.disabled=true; btn.textContent='Guardando…';
  });
  medir();
  var ancho=c.getBoundingClientRect().width;
  window.addEventListener('resize',function(){var a=c.getBoundingClientRect().width;if(Math.abs(a-ancho)>4){ancho=a;medir();}});
})();
