'use strict';
class PlaybackRecovery {
  constructor(restart, scheduled, timers = {setTimeout:(callback,delay)=>setTimeout(callback,delay),clearTimeout:timer=>clearTimeout(timer)}) {
    this.restart=restart;this.scheduled=scheduled;this.timers=timers;this.requested=false;this.attempts=0;
  }
  clear() {this.timers.clearTimeout(this.timer);this.timers.clearTimeout(this.stable);this.timer=null;this.stable=null;}
  begin(reset=true) {this.clear();this.requested=true;if(reset)this.attempts=0;}
  stop() {this.clear();this.requested=false;this.attempts=0;}
  playing() {this.timers.clearTimeout(this.stable);this.stable=this.timers.setTimeout(()=>{if(this.requested)this.attempts=0;},30000);}
  failed() {
    if(!this.requested || this.timer) return;
    this.timers.clearTimeout(this.stable);
    const seconds=Math.min(60,3*2**Math.min(this.attempts++,5));
    this.scheduled(seconds);
    this.timer=this.timers.setTimeout(()=>{this.timer=null;if(this.requested)this.restart();},seconds*1000);
  }
  now() {if(this.requested && this.timer){this.clear();this.restart();}}
}
