package rtsp

import (
	"context"
	"io"
	"net"
	"testing"
	"time"

	"github.com/pion/rtp"
)

func TestForwardVideoUsesAdvertisedPayloadType(t *testing.T) {
	server, client := net.Pipe()
	defer server.Close()
	defer client.Close()
	client.SetReadDeadline(time.Now().Add(time.Second))
	forwarder := NewRTPForwarder()
	if err := forwarder.AddTCPClient("test", server, 0, 2, 4); err != nil {
		t.Fatal(err)
	}
	forwarder.StartClient("test")
	source := &rtp.Packet{Header: rtp.Header{Version: 2, PayloadType: 105, SequenceNumber: 17, Timestamp: 90000, SSRC: 123}, Payload: []byte{1, 2, 3}}
	done := make(chan struct{})
	go func() { forwarder.ForwardVideoPacket(source); close(done) }()
	header := make([]byte, 4)
	if _, err := io.ReadFull(client, header); err != nil {
		t.Fatal(err)
	}
	if header[0] != '$' || header[1] != 0 {
		t.Fatalf("invalid interleaved header: %v", header)
	}
	data := make([]byte, int(header[2])<<8|int(header[3]))
	if _, err := io.ReadFull(client, data); err != nil {
		t.Fatal(err)
	}
	var got rtp.Packet
	if err := got.Unmarshal(data); err != nil {
		t.Fatal(err)
	}
	if got.PayloadType != 96 || got.SequenceNumber != 17 || got.SSRC != 123 {
		t.Fatalf("incorrect forwarded header: %+v", got.Header)
	}
	if source.PayloadType != 105 {
		t.Fatal("source packet mutated")
	}
	<-done
}

func TestSetupDoesNotSendMediaBeforePlay(t *testing.T) {
	server, client := net.Pipe()
	defer server.Close()
	defer client.Close()
	forwarder := NewRTPForwarder()
	if err := forwarder.AddTCPClient("waiting", server, 0, 2, 4); err != nil {
		t.Fatal(err)
	}
	packet := &rtp.Packet{Header: rtp.Header{Version: 2, PayloadType: 96}, Payload: []byte{1}}
	done := make(chan struct{})
	go func() { forwarder.ForwardVideoPacket(packet); forwarder.ForwardAudioPacket(packet); close(done) }()
	select {
	case <-done:
	case <-time.After(time.Second):
		t.Fatal("media was written before PLAY acknowledgement")
	}
	client.SetReadDeadline(time.Now().Add(20 * time.Millisecond))
	if _, err := client.Read(make([]byte, 1)); err == nil {
		t.Fatal("received media before PLAY")
	}
}

func TestFirstVideoPacketCanWaitForPlayWithoutBeingLost(t *testing.T) {
	forwarder := NewRTPForwarder()
	server, client := net.Pipe()
	defer server.Close()
	defer client.Close()
	if err := forwarder.AddTCPClient("reader", server, 0, 2, 4); err != nil {
		t.Fatal(err)
	}
	source := &rtp.Packet{Header: rtp.Header{Version: 2, PayloadType: 96, SequenceNumber: 1}, Payload: []byte{0x40, 1, 2}}
	ctx, cancel := context.WithTimeout(context.Background(), time.Second)
	defer cancel()
	done := make(chan error, 1)
	go func() {
		if err := forwarder.WaitForPlay(ctx); err != nil {
			done <- err
			return
		}
		forwarder.ForwardVideoPacket(source)
		done <- nil
	}()
	client.SetReadDeadline(time.Now().Add(20 * time.Millisecond))
	if _, err := client.Read(make([]byte, 4)); err == nil {
		t.Fatal("initial video sent before PLAY")
	}
	forwarder.StartClient("reader")
	client.SetReadDeadline(time.Now().Add(time.Second))
	data := make([]byte, 4+12+len(source.Payload))
	if _, err := io.ReadFull(client, data); err != nil {
		t.Fatal(err)
	}
	if data[0] != '$' || data[16] != 0x40 {
		t.Fatal("initial codec parameters were lost")
	}
	if err := <-done; err != nil {
		t.Fatal(err)
	}
	pending := NewRTPForwarder()
	cancelled, cancelPending := context.WithCancel(context.Background())
	cancelPending()
	if pending.WaitForPlay(cancelled) == nil {
		t.Fatal("shutdown did not release pending video")
	}
}
