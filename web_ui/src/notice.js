// Local notice preview remains usable even if the desktop connection is lost.
const parse=value=>{
 if(!value||['—','-'].includes(value))return null;
 const text=String(value).slice(0,10),d=new Date(text+'T00:00:00Z');
 if(!/^\d{4}-\d{2}-\d{2}$/.test(text)||Number.isNaN(+d)||d.toISOString().slice(0,10)!==text)throw Error('Enter valid original and revised dates.');
 return d;
};
const format=d=>d.toLocaleDateString('en-GB',{day:'2-digit',month:'short',year:'numeric',timeZone:'UTC'}).toUpperCase();
export function buildNotice(row,reason=''){
 const text=key=>String(row[key]??'').trim();
 const changes=[];
 for(const key of ['etd','eta']){const a=parse(row['at_'+key]),b=parse(row['carrier_'+key]);if(a&&b&&+a!==+b)changes.push({field:key.toUpperCase(),a,b,days:Math.round((b-a)/86400000)});}
 const vesselChanged=Boolean(text('revised_vessel')&&text('revised_vessel').toUpperCase()!==text('vessel').toUpperCase());
 const voyageChanged=Boolean(text('revised_voyage')&&text('revised_voyage').toUpperCase()!==text('voyage').toUpperCase());
 if(!Object.values(row).some(v=>String(v??'').trim())&&!reason.trim())throw Error('Enter at least one schedule detail. Every field is optional.');
 const prefix=Number(row.leg)===2?'SECOND LEG VESSEL ':Number(row.leg)===1&&Number(row.total_legs)>1?'FIRST LEG VESSEL ':'';
 const directions=changes.map(c=>c.days>0?'DELAYED':'ADVANCED');
 let header=changes.length?prefix+(new Set(directions).size===1?changes.map(c=>c.field).join(' AND ')+' CHANGED AND '+directions[0]:changes.map((c,i)=>c.field+' '+directions[i]).join(' AND ')):prefix+'SCHEDULE UPDATE';
 if(vesselChanged||voyageChanged)header=(vesselChanged?'VESSEL CHANGE':'VOYAGE CHANGE')+(changes.length?', '+header:'');
 const revised=changes.length||vesselChanged||voyageChanged;
 const lines=[revised?'PLEASE BE INFORMED THAT THE SUBJECT SHIPMENT SCHEDULE HAS BEEN REVISED.':'PLEASE BE INFORMED OF THE FOLLOWING SHIPMENT SCHEDULE UPDATE.'];
 const details=[];
 const identity=['vessel','voyage'].map(text).filter(Boolean).join(' / ');
 const replacement=['revised_vessel','revised_voyage'].map(text).filter(Boolean).join(' / ');
 if(identity)details.push('VESSEL / VOYAGE: '+identity.toUpperCase());
 if(replacement)details.push('REVISED VESSEL / VOYAGE: '+replacement.toUpperCase());
 if(text('vs_id'))details.push('SCHEDULE: '+text('vs_id').toUpperCase());
 if(text('leg')||text('total_legs'))details.push(text('leg')?'LEG: '+text('leg')+(text('total_legs')?' OF '+text('total_legs'):''):'TOTAL LEGS: '+text('total_legs'));
 for(const key of ['pol','pod'])if(text(key))details.push(key.toUpperCase()+': '+text(key).toUpperCase());
 if(details.length)lines.push('',...details);
 for(const key of ['etd','eta']){const a=parse(row['at_'+key]),b=parse(row['carrier_'+key]),c=changes.find(c=>c.field===key.toUpperCase());if(c){const days=Math.abs(c.days);lines.push('',`${c.field}: ${format(c.a)} → ${format(c.b)}.`,`(${c.days>0?'DELAYED':'ADVANCED'} BY ${days} ${days===1?'DAY':'DAYS'}).`);}else if(a||b)lines.push('',`${a&&b?'':b?'REVISED ':'CURRENT '}${key.toUpperCase()}: ${format(b||a)}.`);}
 if(reason.trim())lines.push('','REASON: '+reason.trim().replace(/\.$/,'').toUpperCase()+'.');
 lines.push('','WE WILL CONTINUE TO MONITOR THE SHIPMENT AND KEEP YOU POSTED.');
 return {header,remarks:lines.join('\n')};
}
