package rtsp

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net"
	"net/url"
	"os"
	"os/exec"
	"os/signal"
	"path/filepath"
	"strconv"
	"strings"
	"syscall"
	"time"

	"github.com/pion/interceptor"
	"github.com/pion/rtp"
	"github.com/pion/webrtc/v4"
)

// BrowserPlayerRequest is passed privately over stdin, never over a public listener.
type BrowserPlayerRequest struct {
	URL string `json:"url"`
	SDP string `json:"sdp"`
}

func validateBrowserRequest(request BrowserPlayerRequest) error {
	u, err := url.Parse(request.URL)
	if err != nil || u.Scheme != "rtsp" || u.Host != "127.0.0.1:8554" || u.User != nil || u.RawQuery != "" || u.Fragment != "" {
		return errors.New("only the local RTSP engine is allowed")
	}
	if len(request.SDP) > 60000 || !strings.Contains(request.SDP, "m=video ") {
		return errors.New("invalid video offer")
	}
	for _, line := range strings.Split(request.SDP, "\n") {
		line = strings.TrimSpace(line)
		if !strings.HasPrefix(line, "a=candidate:") {
			continue
		}
		fields := strings.Fields(line)
		if len(fields) < 8 || fields[6] != "typ" || fields[7] != "host" {
			return errors.New("only local host candidates are allowed")
		}
		host := fields[4]
		if strings.HasSuffix(host, ".local") {
			continue
		}
		ip := net.ParseIP(host)
		if ip == nil || (!ip.IsPrivate() && !ip.IsLoopback() && !ip.IsLinkLocalUnicast()) {
			return errors.New("public media candidates are not allowed")
		}
	}
	return nil
}

func browserCodec(description string) (webrtc.RTPCodecCapability, error) {
	var codec webrtc.RTPCodecCapability
	for _, line := range strings.Split(description, "\n") {
		line = strings.TrimSpace(line)
		if strings.HasPrefix(line, "a=rtpmap:96 ") {
			name := strings.TrimPrefix(line, "a=rtpmap:96 ")
			if name == "H264/90000" {
				codec.MimeType = webrtc.MimeTypeH264
			}
			if name == "H265/90000" {
				codec.MimeType = webrtc.MimeTypeH265
			}
		}
		if strings.HasPrefix(line, "a=fmtp:96 ") {
			codec.SDPFmtpLine = strings.TrimPrefix(line, "a=fmtp:96 ")
		}
	}
	if codec.MimeType == "" {
		return codec, errors.New("unsupported camera codec")
	}
	codec.ClockRate = 90000
	codec.RTCPFeedback = []webrtc.RTCPFeedback{{Type: "nack"}, {Type: "nack", Parameter: "pli"}}
	return codec, nil
}

func browserOfferSupportsCodec(offer string, codec webrtc.RTPCodecCapability) bool {
	name := strings.TrimPrefix(strings.ToLower(codec.MimeType), "video/") + "/90000"
	for _, line := range strings.Split(offer, "\n") {
		fields := strings.Fields(line)
		if len(fields) == 2 && strings.HasPrefix(fields[0], "a=rtpmap:") && strings.ToLower(fields[1]) == name {
			return true
		}
	}
	return false
}

func browserPairIsLocal(pair *webrtc.ICECandidatePair) bool {
	if pair == nil || pair.Local == nil || pair.Remote == nil {
		return false
	}
	for _, candidate := range []*webrtc.ICECandidate{pair.Local, pair.Remote} {
		ip := net.ParseIP(candidate.Address)
		if ip == nil || (!ip.IsPrivate() && !ip.IsLoopback() && !ip.IsLinkLocalUnicast()) || candidate.Typ == webrtc.ICECandidateTypeRelay {
			return false
		}
	}
	return true
}

func browserAPI(codec webrtc.RTPCodecCapability) (*webrtc.API, func(), error) {
	m := &webrtc.MediaEngine{}
	if err := m.RegisterCodec(webrtc.RTPCodecParameters{RTPCodecCapability: codec, PayloadType: 96}, webrtc.RTPCodecTypeVideo); err != nil {
		return nil, nil, err
	}
	registry := &interceptor.Registry{}
	if err := webrtc.RegisterDefaultInterceptors(m, registry); err != nil {
		return nil, nil, err
	}
	settings := webrtc.SettingEngine{}
	settings.SetNetworkTypes([]webrtc.NetworkType{webrtc.NetworkTypeTCP4})
	settings.SetIncludeLoopbackCandidate(os.Getenv("TUYA_WEBRTC_HOST_IP") == "")
	settings.SetIPFilter(func(ip net.IP) bool { return ip.IsPrivate() || ip.IsLoopback() })
	if host := os.Getenv("TUYA_WEBRTC_HOST_IP"); host != "" {
		ip := net.ParseIP(host)
		if ip == nil || !ip.IsPrivate() {
			return nil, nil, errors.New("browser WebRTC requires a private LAN address")
		}
		settings.SetNAT1To1IPs([]string{host}, webrtc.ICECandidateTypeHost)
	}
	// ICE over TCP avoids Docker Desktop's UDP source-address rewriting.
	// It still sends RTP immediately, without MP4 buffering or transcoding.
	var listener net.Listener
	var err error
	for port := 52100; port <= 52119; port++ {
		listener, err = net.Listen("tcp", ":"+strconv.Itoa(port))
		if err == nil {
			break
		}
	}
	if err != nil {
		return nil, nil, err
	}
	mux := webrtc.NewICETCPMux(nil, listener, 8)
	settings.SetICETCPMux(mux)
	settings.SetLite(true)
	return webrtc.NewAPI(webrtc.WithMediaEngine(m), webrtc.WithInterceptorRegistry(registry), webrtc.WithSettingEngine(settings)), func() { _ = mux.Close() }, nil
}

func browserRemuxCommand(ctx context.Context, source, description string, port int) *exec.Cmd {
	return exec.CommandContext(ctx, "ffmpeg", "-hide_banner", "-loglevel", "error", "-rtsp_transport", "tcp", "-timeout", "110000000", "-allowed_media_types", "video", "-analyzeduration", "1000000", "-probesize", "1000000", "-i", source, "-map", "0:v:0", "-an", "-c:v", "copy", "-payload_type", "96", "-sdp_file", description, "-f", "rtp", "rtp://127.0.0.1:"+strconv.Itoa(port)+"?pkt_size=1200")
}

// RunBrowserPlayer remuxes camera RTP into a local, encrypted browser peer.
// No video is encoded, written to disk, or sent to a relay.
func RunBrowserPlayer(input io.Reader, output io.Writer) error {
	var request BrowserPlayerRequest
	if err := json.NewDecoder(io.LimitReader(input, 65536)).Decode(&request); err != nil {
		return err
	}
	if err := validateBrowserRequest(request); err != nil {
		return err
	}
	ctx, cancel := signal.NotifyContext(context.Background(), syscall.SIGTERM, syscall.SIGINT)
	defer cancel()
	socket, err := net.ListenUDP("udp4", &net.UDPAddr{IP: net.IPv4(127, 0, 0, 1)})
	if err != nil {
		return err
	}
	defer socket.Close()
	dir, err := os.MkdirTemp("", "tuya-browser-")
	if err != nil {
		return err
	}
	defer os.RemoveAll(dir)
	description := filepath.Join(dir, "media.sdp")
	command := browserRemuxCommand(ctx, request.URL, description, socket.LocalAddr().(*net.UDPAddr).Port)
	if err := command.Start(); err != nil {
		return err
	}
	finished := make(chan error, 1)
	go func() { finished <- command.Wait() }()
	defer func() { cancel(); _ = command.Process.Kill(); <-finished }()
	// Buffer the first keyframe while ICE/DTLS connects, so joining does not lose it.
	packets := make(chan *rtp.Packet, 2048)
	go func() {
		data := make([]byte, 2048)
		defer close(packets)
		for {
			count, _, err := socket.ReadFromUDP(data)
			if err != nil {
				return
			}
			packet := &rtp.Packet{}
			if packet.Unmarshal(data[:count]) != nil {
				continue
			}
			select {
			case packets <- packet.Clone():
			case <-ctx.Done():
				return
			}
		}
	}()
	startup := time.NewTimer(105 * time.Second)
	defer startup.Stop()
	poll := time.NewTicker(50 * time.Millisecond)
	defer poll.Stop()
	var codec webrtc.RTPCodecCapability
	for codec.MimeType == "" {
		select {
		case <-ctx.Done():
			return ctx.Err()
		case <-startup.C:
			return errors.New("camera startup timeout")
		case result := <-finished:
			finished <- result
			return errors.New("camera stream ended")
		case <-poll.C:
			data, err := os.ReadFile(description)
			if err == nil {
				codec, err = browserCodec(string(data))
				if err != nil {
					return err
				}
			}
		}
	}
	if !browserOfferSupportsCodec(request.SDP, codec) {
		return errors.New("browser cannot decode camera codec")
	}
	api, closeTransport, err := browserAPI(codec)
	if err != nil {
		return err
	}
	defer closeTransport()
	peer, err := api.NewPeerConnection(webrtc.Configuration{})
	if err != nil {
		return err
	}
	defer peer.Close()
	track, err := webrtc.NewTrackLocalStaticRTP(codec, "video", "tuya-local")
	if err != nil {
		return err
	}
	sender, err := peer.AddTrack(track)
	if err != nil {
		return err
	}
	go func() {
		buffer := make([]byte, 1500)
		for {
			if _, _, err := sender.Read(buffer); err != nil {
				return
			}
		}
	}()
	connected := make(chan struct{}, 1)
	peer.OnConnectionStateChange(func(state webrtc.PeerConnectionState) {
		if state == webrtc.PeerConnectionStateConnected {
			select {
			case connected <- struct{}{}:
			default:
			}
		}
		if state == webrtc.PeerConnectionStateFailed || state == webrtc.PeerConnectionStateClosed {
			cancel()
		}
	})
	if err := peer.SetRemoteDescription(webrtc.SessionDescription{Type: webrtc.SDPTypeOffer, SDP: request.SDP}); err != nil {
		return err
	}
	answer, err := peer.CreateAnswer(nil)
	if err != nil {
		return err
	}
	gathered := webrtc.GatheringCompletePromise(peer)
	if err := peer.SetLocalDescription(answer); err != nil {
		return err
	}
	select {
	case <-gathered:
	case <-ctx.Done():
		return ctx.Err()
	case <-time.After(5 * time.Second):
		return errors.New("ICE gathering timeout")
	}
	if err := json.NewEncoder(output).Encode(map[string]string{"sdp": peer.LocalDescription().SDP, "type": "answer", "codec": codec.MimeType}); err != nil {
		return err
	}
	select {
	case <-connected:
	case <-ctx.Done():
		return ctx.Err()
	case <-time.After(15 * time.Second):
		return errors.New("browser connection timeout")
	}
	for {
		select {
		case <-ctx.Done():
			return nil
		case result := <-finished:
			finished <- result
			return fmt.Errorf("camera stream ended")
		case packet, ok := <-packets:
			if !ok {
				return errors.New("RTP input closed")
			}
			pair, err := sender.Transport().ICETransport().GetSelectedCandidatePair()
			if err != nil || !browserPairIsLocal(pair) {
				return errors.New("browser media route is not local")
			}
			if err := track.WriteRTP(packet); err != nil {
				return err
			}
		}
	}
}
