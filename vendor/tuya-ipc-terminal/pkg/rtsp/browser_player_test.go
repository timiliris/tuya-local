package rtsp

import (
	"context"
	"encoding/binary"
	"github.com/pion/stun/v3"
	"io"
	"net"
	"strings"
	"testing"
	"time"

	"github.com/pion/webrtc/v4"
)

func TestBrowserRequestRestrictsMediaToLocalEngineAndCandidates(t *testing.T) {
	base := "v=0\r\nm=video 9 UDP/TLS/RTP/SAVPF 96\r\n"
	for _, candidate := range []string{"192.168.50.10", "127.0.0.1", "fd00::1", "browser.local"} {
		if err := validateBrowserRequest(BrowserPlayerRequest{URL: "rtsp://127.0.0.1:8554/Camera/hd", SDP: base + "a=candidate:1 1 udp 123 " + candidate + " 5000 typ host\r\n"}); err != nil {
			t.Fatal(err)
		}
	}
	for _, candidate := range []string{"8.8.8.8 5000 typ host", "192.168.50.40 5000 typ relay", "192.168.50.40 5000 typ srflx", "evil.example 5000 typ host"} {
		if validateBrowserRequest(BrowserPlayerRequest{URL: "rtsp://127.0.0.1:8554/Camera/hd", SDP: base + "a=candidate:1 1 udp 123 " + candidate + "\r\n"}) == nil {
			t.Fatalf("accepted public/relay candidate %s", candidate)
		}
	}
	for _, source := range []string{"rtsp://public.example/hd", "http://127.0.0.1:8554/hd", "rtsp://user:secret@127.0.0.1:8554/hd", "rtsp://127.0.0.1:8554/hd?redirect=public"} {
		if validateBrowserRequest(BrowserPlayerRequest{URL: source, SDP: base}) == nil {
			t.Fatalf("accepted %s", source)
		}
	}
}

func TestBrowserCodecUsesActualCameraCodecAndParameters(t *testing.T) {
	for _, name := range []string{"H264", "H265"} {
		codec, err := browserCodec("v=0\r\na=rtpmap:96 " + name + "/90000\r\na=fmtp:96 packetization-mode=1;profile-level-id=42e01f\r\n")
		if err != nil || codec.MimeType != "video/"+name || codec.ClockRate != 90000 || !strings.Contains(codec.SDPFmtpLine, "42e01f") {
			t.Fatalf("unexpected codec: %#v %v", codec, err)
		}
	}
	if _, err := browserCodec("a=rtpmap:96 VP8/90000"); err == nil {
		t.Fatal("accepted unsupported codec")
	}
}

func TestBrowserRemuxNeverTranscodesOrUsesRemoteOutput(t *testing.T) {
	command := browserRemuxCommand(context.Background(), "rtsp://127.0.0.1:8554/Camera/hd", "/tmp/media.sdp", 12345)
	args := strings.Join(command.Args, " ")
	if !strings.Contains(args, "-c:v copy") || strings.Contains(args, "libx264") || !strings.HasSuffix(args, "rtp://127.0.0.1:12345?pkt_size=1200") {
		t.Fatal(args)
	}
}

func TestBrowserAPINegotiatesH264AndRejectsUnsupportedHD(t *testing.T) {
	t.Setenv("TUYA_WEBRTC_HOST_IP", "")
	receiverAPI, closeReceiver, err := browserAPI(webrtc.RTPCodecCapability{MimeType: webrtc.MimeTypeH264, ClockRate: 90000, SDPFmtpLine: "packetization-mode=1;profile-level-id=42e01f"})
	if err != nil {
		t.Fatal(err)
	}
	defer closeReceiver()
	receiver, err := receiverAPI.NewPeerConnection(webrtc.Configuration{})
	if err != nil {
		t.Fatal(err)
	}
	defer receiver.Close()
	if _, err = receiver.AddTransceiverFromKind(webrtc.RTPCodecTypeVideo, webrtc.RTPTransceiverInit{Direction: webrtc.RTPTransceiverDirectionRecvonly}); err != nil {
		t.Fatal(err)
	}
	offer, err := receiver.CreateOffer(nil)
	if err != nil {
		t.Fatal(err)
	}
	for _, mime := range []string{webrtc.MimeTypeH264, webrtc.MimeTypeH265} {
		api, closeAPI, err := browserAPI(webrtc.RTPCodecCapability{MimeType: mime, ClockRate: 90000, SDPFmtpLine: "packetization-mode=1;profile-level-id=42e01f"})
		if err != nil {
			t.Fatal(err)
		}
		sender, err := api.NewPeerConnection(webrtc.Configuration{})
		if err != nil {
			t.Fatal(err)
		}
		track, _ := webrtc.NewTrackLocalStaticRTP(webrtc.RTPCodecCapability{MimeType: mime, ClockRate: 90000}, "video", "test")
		_, err = sender.AddTrack(track)
		if err != nil {
			t.Fatal(err)
		}
		err = sender.SetRemoteDescription(offer)
		if mime == webrtc.MimeTypeH264 && err != nil {
			t.Fatal(err)
		}
		supported := browserOfferSupportsCodec(offer.SDP, webrtc.RTPCodecCapability{MimeType: mime})
		if supported != (mime == webrtc.MimeTypeH264) {
			t.Fatal("incorrect codec compatibility detection")
		}

		sender.Close()
		closeAPI()
	}
}

func TestBrowserMediaRejectsPublicPeerReflexiveRoutesBeforeSendingRTP(t *testing.T) {
	local := &webrtc.ICECandidate{Address: "192.168.50.10", Typ: webrtc.ICECandidateTypeHost}
	for _, remote := range []struct {
		address string
		kind    webrtc.ICECandidateType
		allowed bool
	}{
		{"192.168.50.30", webrtc.ICECandidateTypeHost, true},
		{"127.0.0.1", webrtc.ICECandidateTypePrflx, true},
		{"172.217.16.241", webrtc.ICECandidateTypePrflx, false},
		{"192.168.50.30", webrtc.ICECandidateTypeRelay, false},
	} {
		pair := &webrtc.ICECandidatePair{Local: local, Remote: &webrtc.ICECandidate{Address: remote.address, Typ: remote.kind}}
		if browserPairIsLocal(pair) != remote.allowed {
			t.Fatalf("wrong route policy for %s", remote.address)
		}
	}
	if browserPairIsLocal(nil) {
		t.Fatal("missing pair accepted")
	}
}

func TestBrowserTCPMuxAnswersSTUNOnMappedLANInterface(t *testing.T) {
	var host string
	addresses, _ := net.InterfaceAddrs()
	for _, address := range addresses {
		ip, _, _ := net.ParseCIDR(address.String())
		if ip != nil && ip.To4() != nil && ip.IsPrivate() {
			host = ip.String()
			break
		}
	}
	if host == "" {
		t.Skip("no private LAN interface")
	}
	t.Setenv("TUYA_WEBRTC_HOST_IP", host)
	codec := webrtc.RTPCodecCapability{MimeType: webrtc.MimeTypeH264, ClockRate: 90000, SDPFmtpLine: "packetization-mode=1;profile-level-id=42e01f"}
	api, closeAPI, err := browserAPI(codec)
	if err != nil {
		t.Fatal(err)
	}
	defer closeAPI()
	receiver, err := webrtc.NewPeerConnection(webrtc.Configuration{})
	if err != nil {
		t.Fatal(err)
	}
	defer receiver.Close()
	receiver.AddTransceiverFromKind(webrtc.RTPCodecTypeVideo, webrtc.RTPTransceiverInit{Direction: webrtc.RTPTransceiverDirectionRecvonly})
	offer, _ := receiver.CreateOffer(nil)
	receiver.SetLocalDescription(offer)
	peer, err := api.NewPeerConnection(webrtc.Configuration{})
	if err != nil {
		t.Fatal(err)
	}
	defer peer.Close()
	track, _ := webrtc.NewTrackLocalStaticRTP(codec, "video", "test")
	peer.AddTrack(track)
	if err := peer.SetRemoteDescription(offer); err != nil {
		t.Fatal(err)
	}
	answer, err := peer.CreateAnswer(nil)
	if err != nil {
		t.Fatal(err)
	}
	gathered := webrtc.GatheringCompletePromise(peer)
	if err := peer.SetLocalDescription(answer); err != nil {
		t.Fatal(err)
	}
	select {
	case <-gathered:
	case <-time.After(3 * time.Second):
		t.Fatal("gather timeout")
	}
	value := func(sdp, prefix string) string {
		for _, line := range strings.Split(sdp, "\n") {
			if strings.HasPrefix(line, prefix) {
				return strings.TrimSpace(strings.TrimPrefix(line, prefix))
			}
		}
		return ""
	}
	local := peer.LocalDescription().SDP
	candidate := strings.Fields(value(local, "a=candidate:"))
	if len(candidate) < 6 || candidate[2] != "tcp" {
		t.Fatalf("missing TCP candidate: %v", candidate)
	}
	connection, err := net.DialTimeout("tcp", net.JoinHostPort(host, candidate[5]), time.Second)
	if err != nil {
		t.Fatal(err)
	}
	defer connection.Close()
	connection.SetDeadline(time.Now().Add(2 * time.Second))
	message, err := stun.Build(stun.TransactionID, stun.BindingRequest,
		stun.NewUsername(value(local, "a=ice-ufrag:")+":"+value(offer.SDP, "a=ice-ufrag:")),
		stun.RawAttribute{Type: stun.AttrPriority, Value: []byte{0x6e, 0, 0, 1}},
		stun.RawAttribute{Type: stun.AttrICEControlling, Value: []byte{0, 0, 0, 0, 0, 0, 0, 1}},
		stun.RawAttribute{Type: stun.AttrUseCandidate},
		stun.NewShortTermIntegrity(value(local, "a=ice-pwd:")), stun.Fingerprint)
	if err != nil {
		t.Fatal(err)
	}
	frame := make([]byte, 2+len(message.Raw))
	binary.BigEndian.PutUint16(frame, uint16(len(message.Raw)))
	copy(frame[2:], message.Raw)
	if _, err := connection.Write(frame); err != nil {
		t.Fatal(err)
	}
	header := make([]byte, 2)
	if _, err := io.ReadFull(connection, header); err != nil {
		t.Fatalf("mapped TCP interface did not answer ICE: %v", err)
	}
	response := make([]byte, binary.BigEndian.Uint16(header))
	if _, err := io.ReadFull(connection, response); err != nil {
		t.Fatal(err)
	}
	decoded := &stun.Message{Raw: response}
	if err := decoded.Decode(); err != nil || decoded.Type != stun.BindingSuccess {
		t.Fatalf("invalid STUN response %v %v", decoded.Type, err)
	}
}

func TestBrowserPlayerUsesConfiguredLoopbackPort(t *testing.T) {
	t.Setenv("TUYA_RTSP_PORT", "18554")
	request := BrowserPlayerRequest{URL: "rtsp://127.0.0.1:18554/Camera/hd", SDP: "v=0\r\nm=video 9 UDP/TLS/RTP/SAVPF 96\r\n"}
	if err := validateBrowserRequest(request); err != nil {
		t.Fatal(err)
	}
	request.URL = "rtsp://127.0.0.1:8554/Camera/hd"
	if validateBrowserRequest(request) == nil {
		t.Fatal("accepted the other runtime's RTSP port")
	}
}

func TestBrowserAPIRejectsInvalidPortPool(t *testing.T) {
	t.Setenv("TUYA_BROWSER_TCP_MIN", "65535")
	_, _, err := browserAPI(webrtc.RTPCodecCapability{MimeType: webrtc.MimeTypeH264, ClockRate: 90000})
	if err == nil {
		t.Fatal("accepted an overflowing TCP port range")
	}
}
