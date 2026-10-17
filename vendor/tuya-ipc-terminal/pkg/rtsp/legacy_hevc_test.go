package rtsp

import (
	"bytes"
	"github.com/pion/rtp"
	"github.com/pion/rtp/codecs"
	"testing"
)

func TestLegacyHEVCAggregation(t *testing.T) {
	source := &rtp.Packet{Header: rtp.Header{SequenceNumber: 8, Timestamp: 123, Marker: true}, Payload: []byte{24, 0, 3, 0x40, 1, 0x11, 0, 3, 0x42, 1, 0x22}}
	var n legacyHEVCPacketizer
	out := n.convert(source)
	if out == nil || out.SequenceNumber != 8 || out.Timestamp != 123 || !out.Marker {
		t.Fatal("lost RTP metadata")
	}
	var parsed codecs.H265AggregationPacket
	if _, err := parsed.Unmarshal(out.Payload); err != nil {
		t.Fatalf("invalid HEVC aggregation: %v", err)
	}
	if !bytes.Equal(out.Payload, []byte{0x60, 1, 0, 3, 0x40, 1, 0x11, 0, 3, 0x42, 1, 0x22}) {
		t.Fatalf("wrong aggregation: %x", out.Payload)
	}
	if source.Payload[0] != 24 {
		t.Fatal("modified input")
	}
}

func TestLegacyHEVCFragmentsAndLoss(t *testing.T) {
	var n legacyHEVCPacketizer
	parts := [][]byte{{0x3c, 0x86, 1, 0xaa}, {0x3c, 0x06, 0xbb}, {0x3c, 0x46, 0xcc}}
	want := [][]byte{{0x62, 1, 0x93, 0xaa}, {0x62, 1, 0x13, 0xbb}, {0x62, 1, 0x53, 0xcc}}
	for i, p := range parts {
		out := n.convert(&rtp.Packet{Header: rtp.Header{SequenceNumber: uint16(65534 + i), Timestamp: 22}, Payload: p})
		if out == nil || !bytes.Equal(out.Payload, want[i]) {
			t.Fatalf("fragment %d: %v", i, out)
		}
		var parsed codecs.H265FragmentationUnitPacket
		if _, err := parsed.Unmarshal(out.Payload); err != nil {
			t.Fatal(err)
		}
	}
	n.convert(&rtp.Packet{Header: rtp.Header{SequenceNumber: 7, Timestamp: 22}, Payload: parts[0]})
	if n.convert(&rtp.Packet{Header: rtp.Header{SequenceNumber: 9, Timestamp: 22}, Payload: parts[2]}) != nil {
		t.Fatal("forwarded damaged fragment sequence")
	}
	if n.convert(&rtp.Packet{Payload: []byte{24, 0, 7, 0x40, 1}}) != nil {
		t.Fatal("accepted truncated aggregation")
	}
}

func TestLegacyHEVCLeavesNativePacketsUnchanged(t *testing.T) {
	var n legacyHEVCPacketizer
	p := &rtp.Packet{Payload: []byte{0x40, 1, 0x55}}
	if n.convert(p) != p {
		t.Fatal("modified native HEVC NAL")
	}
}

func TestHEVCRTPUsesLegacyOfferAndHDClarity(t *testing.T) {
	t.Setenv("TUYA_HEVC_TRANSPORT", "rtp")
	b := &WebRTCBridge{isHEVC: true, streamType: 2}
	if b.offerStreamType() != 1 {
		t.Fatal("wrong legacy RTP offer")
	}
	b.isHEVC = false
	b.streamType = 4
	if b.offerStreamType() != 4 {
		t.Fatal("changed SD offer")
	}
}

func TestInitialSDPacketsCannotDeclareHDReady(t *testing.T) {
	var n legacyHEVCPacketizer
	for _, payload := range [][]byte{
		{0x67, 0x42, 0, 0x1e},
		{24, 0, 4, 0x67, 0x42, 0, 0x1e},
		{0x7c, 0x85, 0x88, 0xaa},
		{0x41, 1, 0x11},
	} {
		if n.convert(&rtp.Packet{Payload: payload}) != nil || n.parameters {
			t.Fatal("initial H264 accepted as HEVC")
		}
	}
	n.convert(&rtp.Packet{Payload: []byte{0x26, 1, 0x11}})
	if n.parameters {
		t.Fatal("slice declared codec headers ready")
	}
	n.convert(&rtp.Packet{Payload: []byte{24, 0, 3, 0x40, 1, 0x11}})
	if !n.parameters {
		t.Fatal("actual HEVC VPS was not recognized")
	}
}

func TestNativeHEVCParametersAndFragmentsRemainSupported(t *testing.T) {
	var n legacyHEVCPacketizer
	aggregate := &rtp.Packet{Payload: []byte{0x60, 1, 0, 3, 0x40, 1, 0x11}}
	if n.convert(aggregate) != aggregate || !n.parameters {
		t.Fatal("native HEVC aggregation lost")
	}
	fragment := &rtp.Packet{Payload: []byte{0x62, 1, 0x93, 0x11}}
	if n.convert(fragment) != fragment {
		t.Fatal("native HEVC fragment lost")
	}
	var other legacyHEVCPacketizer
	if other.convert(&rtp.Packet{Payload: []byte{24, 0, 3, 0x40, 1, 0x11, 0, 7, 0x42, 1}}) != nil || other.parameters {
		t.Fatal("invalid aggregation marked HD ready")
	}
}
