'use strict';
// Signaling stays on this origin. No STUN/TURN server is used for browser media.
class LocalWebRTC {
  static supported() { return typeof RTCPeerConnection === 'function'; }
  constructor() {
    this.peer = new RTCPeerConnection({iceServers: [], bundlePolicy: 'max-bundle'});
    this.closed = false;
  }
  close() {
    this.closed = true;
    clearTimeout(this.timer); clearTimeout(this.disconnectTimer); clearInterval(this.watchdog);
    this.peer.onconnectionstatechange = null;
    this.peer.close();
    this.cancel?.();
  }
  async play(video, exchange, signal, onFailure) {
    const peer = this.peer;
    const aborted = () => this.close();
    signal.addEventListener('abort', aborted, {once:true});
    if (signal.aborted) {this.close(); throw new DOMException('Stopped','AbortError');}
    peer.addTransceiver('video', {direction:'recvonly'});
    video.muted = true;
    const firstImage = new Promise((resolve,reject) => {
      this.cancel = () => reject(new DOMException('Stopped','AbortError'));
      this.fail = error => {
        if (this.closed || this.failed) return;
        this.failed=true;
        reject(error);
        onFailure(error);
      };
      peer.ontrack = event => {
        if (this.closed) return;
        video.srcObject = new MediaStream([event.track]);
        video.play().catch(error => this.fail(error));
      };
      video.onplaying = () => {
        if (this.closed || !video.videoWidth) return;
        clearTimeout(this.timer);
        resolve();
      };
      peer.onconnectionstatechange = () => {
        clearTimeout(this.disconnectTimer);
        if(peer.connectionState==='disconnected')this.disconnectTimer=setTimeout(()=>this.fail(new Error('WebRTC disconnected')),8000);
        if (peer.connectionState === 'failed' || peer.connectionState === 'closed') this.fail(new Error('WebRTC connection failed'));
      };
    });
    // Attach the rejection handler before asynchronous ICE/signaling begins.
    firstImage.catch(() => {});
    try {
      await peer.setLocalDescription(await peer.createOffer());
      await new Promise((resolve,reject) => {
        const cleanup = () => {clearTimeout(timer); peer.removeEventListener('icegatheringstatechange',check); signal.removeEventListener('abort',cancel);};
        const check = () => {if(peer.iceGatheringState === 'complete'){cleanup();resolve();}};
        const cancel = () => {cleanup();reject(new DOMException('Stopped','AbortError'));};
        const timer = setTimeout(() => {cleanup();reject(new Error('ICE gathering timeout'));},5000);
        peer.addEventListener('icegatheringstatechange',check);
        signal.addEventListener('abort',cancel,{once:true});
        check(); if (signal.aborted) cancel();
      });
      const answer = await exchange(peer.localDescription.sdp);
      if (this.closed || signal.aborted) throw new DOMException('Stopped','AbortError');
      await peer.setRemoteDescription({type:'answer',sdp:answer.sdp});
      this.timer = setTimeout(() => this.fail(new Error('No decoded WebRTC video')),15000);
      await firstImage;
      let lastFrames = -1, lastProgress = Date.now();
      this.watchdog = setInterval(async () => {
        if(this.closed) return;
        try {
          const stats = await peer.getStats();
          let frames = 0;
          stats.forEach(item => {if(item.type === 'inbound-rtp' && item.kind === 'video') frames += item.framesDecoded || 0;});
          if(frames > lastFrames) {lastFrames=frames;lastProgress=Date.now();}
          else if(Date.now()-lastProgress > 12000) this.fail(new Error('WebRTC video stalled'));
        } catch {this.fail(new Error('WebRTC statistics unavailable'));}
      },3000);
      return answer.codec;
    } catch(error) {
      this.close();
      throw error;
    } finally {
      signal.removeEventListener('abort',aborted);
    }
  }
}
