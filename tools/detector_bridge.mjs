// One detector implementation is shared by the Chrome companion and all server connectors.
import readline from 'node:readline';
import {Detector} from '../extension/detector.mjs';
let detector;
for await (const line of readline.createInterface({input:process.stdin,crlfDelay:Infinity})) {
  try {
    const m=JSON.parse(line);
    if(m.type==='init')detector=new Detector(m.options);
    if(!detector)throw Error('Initialize detector first');
    if(m.type==='mark')detector.trigger(m.at,'Manual highlight');
    if(m.type==='flush'){process.stdout.write(JSON.stringify({clip:detector.flush(m.at)})+'\n');continue;}
    if(m.messages)detector.add(m.at,m.messages);
    process.stdout.write(JSON.stringify(detector.tick(m.at || 0,m.rms || 0))+'\n');
  } catch(e){process.stdout.write(JSON.stringify({error:e.message})+'\n');}
}
