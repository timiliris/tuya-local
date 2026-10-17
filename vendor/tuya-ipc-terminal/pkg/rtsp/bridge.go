package rtsp

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"net/http/cookiejar"
	"net/url"
	"os"
	"os/exec"
	"regexp"
	"strings"
	"sync"
	"time"

	pion "github.com/pion/webrtc/v4"
	"golang.org/x/net/publicsuffix"

	"tuya-ipc-terminal/pkg/core"
	"tuya-ipc-terminal/pkg/storage"
	"tuya-ipc-terminal/pkg/tuya"
	"tuya-ipc-terminal/pkg/utils"
	"tuya-ipc-terminal/pkg/webrtc"

	"github.com/pion/rtp"
)

type WebRTCBridge struct {
	camera         *storage.CameraInfo
	resolution     string
	offerSDP       string
	streamType     int
	isHEVC         bool
	user           *storage.UserSession
	storageManager *storage.StorageManager

	// WebRTC components
	peerConnection   *pion.PeerConnection
	dataChannel      *pion.DataChannel
	mqttClient       *tuya.MQTTClient
	mobileWakeClient *tuya.MQTTClient
	cameraClient     *tuya.MQTTCameraClient
	rtpForwarder     *RTPForwarder

	// State
	wakeReady       chan struct{}
	wakeOnce        sync.Once
	wakeError       error
	activeAttempt   *WebRTCBridge
	connected       bool
	connectionReady chan error
	connectionOnce  sync.Once
	mutex           sync.RWMutex

	// Context for cancellation
	ctx    context.Context
	cancel context.CancelFunc

	// RTP forwarding
	videoTrack  *pion.TrackRemote
	audioTrack  *pion.TrackRemote
	backchannel *pion.TrackLocalStaticRTP

	// Callbacks
	OnVideoPacket func(packet *rtp.Packet)
	OnAudioPacket func(packet *rtp.Packet)
	OnError       func(error)
}

func NewWebRTCBridge(camera *storage.CameraInfo, streamResolution string, user *storage.UserSession, storageManager *storage.StorageManager) *WebRTCBridge {
	ctx, cancel := context.WithCancel(context.Background())

	wb := &WebRTCBridge{
		camera:          camera,
		resolution:      streamResolution,
		user:            user,
		rtpForwarder:    NewRTPForwarder(),
		storageManager:  storageManager,
		connected:       false,
		wakeReady:       make(chan struct{}),
		connectionReady: make(chan error, 1),
		ctx:             ctx,
		cancel:          cancel,
	}

	wb.rtpForwarder.OnBackchannelAudio = wb.ForwardBackchannelAudioPacket

	return wb
}

func (wb *WebRTCBridge) signalConnection(err error) {
	wb.connectionOnce.Do(func() { wb.connectionReady <- err })
}

func (wb *WebRTCBridge) waitForConnection(timeout time.Duration) error {
	return wb.waitForConnectionRetry(timeout, 0, nil)
}

func (wb *WebRTCBridge) waitForConnectionRetry(timeout, interval time.Duration, retry func(int)) error {
	timer := time.NewTimer(timeout)
	defer timer.Stop()
	var ticks <-chan time.Time
	if interval > 0 && retry != nil {
		ticker := time.NewTicker(interval)
		defer ticker.Stop()
		ticks = ticker.C
	}
	attempt := 0
	for {
		select {
		case err := <-wb.connectionReady:
			return err
		case <-wb.ctx.Done():
			return wb.ctx.Err()
		case <-timer.C:
			return errors.New("camera did not deliver video before startup deadline")
		case <-ticks:
			attempt++
			retry(attempt)
		}
	}
}

func (wb *WebRTCBridge) finishWake(err error) {
	wb.wakeOnce.Do(func() { wb.wakeError = err; close(wb.wakeReady) })
}

func (wb *WebRTCBridge) waitForWake(ctx context.Context) error {
	select {
	case <-wb.wakeReady:
		return wb.wakeError
	case <-ctx.Done():
		return ctx.Err()
	case <-wb.ctx.Done():
		return wb.ctx.Err()
	}
}

func (wb *WebRTCBridge) Start() (startError error) {
	defer func() { wb.finishWake(startError) }()
	ctx, cancel := context.WithTimeout(wb.ctx, 90*time.Second)
	defer cancel()
	return retryCameraStart(ctx, 3, 2*time.Second, func(attempt int) error {
		if attempt > 1 {
			core.Logger.Info().Int("attempt", attempt).Msg("Retrying camera wake and local video connection")
		}
		child := NewWebRTCBridge(wb.camera, wb.resolution, wb.user, wb.storageManager)
		child.cancel()
		child.ctx, child.cancel = context.WithCancel(wb.ctx)
		stopDeadline := context.AfterFunc(ctx, child.cancel)
		child.rtpForwarder = wb.rtpForwarder
		child.OnVideoPacket, child.OnAudioPacket = wb.OnVideoPacket, wb.OnAudioPacket
		child.OnError = func(err error) {
			child.signalConnection(err)
			if child.ctx.Err() == nil {
				wb.connectionFailed(err)
			}
		}
		wb.mutex.Lock()
		wb.activeAttempt = child
		wb.mutex.Unlock()
		if err := child.startAttempt(); err != nil {
			stopDeadline()
			child.stop(false)
			return err
		}
		stopDeadline()
		wb.mutex.Lock()
		defer wb.mutex.Unlock()
		if err := ctx.Err(); err != nil {
			child.stop(false)
			return err
		}
		// A successful session must outlive the bounded startup context.
		wb.connected = true
		return nil
	})
}

func (wb *WebRTCBridge) connectionFailed(err error) {
	wb.mutex.Lock()
	wasConnected := wb.connected
	wb.connected = false
	wb.mutex.Unlock()
	if wasConnected && wb.ctx.Err() == nil && wb.OnError != nil {
		wb.OnError(err)
	}
}

func retryCameraStart(ctx context.Context, attempts int, delay time.Duration, start func(int) error) error {
	var lastError error
	for attempt := 1; attempt <= attempts; attempt++ {
		if err := ctx.Err(); err != nil {
			return err
		}
		if lastError = start(attempt); lastError == nil {
			return nil
		}
		if attempt == attempts {
			break
		}
		timer := time.NewTimer(delay)
		select {
		case <-ctx.Done():
			timer.Stop()
			return ctx.Err()
		case <-timer.C:
		}
	}
	return fmt.Errorf("camera unavailable after %d attempts: %w", attempts, lastError)
}

func startupCameraSkill(live, cached string) (tuya.Skill, error) {
	var skill, known tuya.Skill
	if err := json.Unmarshal([]byte(live), &skill); err != nil {
		return skill, fmt.Errorf("failed to parse skill info: %w", err)
	}
	if err := json.Unmarshal([]byte(cached), &known); err != nil {
		if len(skill.Videos) == 0 {
			return skill, fmt.Errorf("failed to read cached camera capabilities: %w", err)
		}
		return skill, nil
	}
	// LowPower is a device capability; an omitted live field must not disable wake-up.
	if skill.LowPower == 0 {
		skill.LowPower = known.LowPower
	}
	if len(skill.Videos) == 0 {
		skill.Videos = known.Videos
	}
	if len(skill.Audios) == 0 {
		skill.Audios = known.Audios
	}
	return skill, nil
}

func (wb *WebRTCBridge) startAttempt() error {
	var startupWakeClient *tuya.MQTTClient
	var startupLocalKey string
	wb.mutex.Lock()
	defer wb.mutex.Unlock()

	core.Logger.Info().Msgf("Starting WebRTC bridge for camera: %s", wb.camera.DeviceName)

	// Create HTTP client with session
	httpClient := wb.createHTTPClient()
	if httpClient == nil {
		return errors.New("failed to create HTTP client")
	}

	// Get app info
	appInfo, err := tuya.GetAppInfo(httpClient, wb.user.SessionData.ServerHost)
	if err != nil {
		return fmt.Errorf("failed to get app info: %v", err)
	}

	// Get MQTT config
	mqttConfig, err := tuya.GetMQTTConfig(httpClient, wb.user.SessionData.ServerHost)
	if err != nil {
		return fmt.Errorf("failed to get MQTT config: %v", err)
	}

	// Connect to MQTT broker
	wb.mqttClient, err = tuya.NewMqttClient(
		appInfo.Result.ClientId,
		wb.user.SessionData.LoginResult.Domain.MobileMqttsUrl,
		&mqttConfig.Result,
		"https://"+wb.user.SessionData.ServerHost,
	)

	if err != nil {
		return fmt.Errorf("failed to connect to MQTT: %v", err)
	}

	if err = wb.mqttClient.Connected.Wait(); err != nil {
		return fmt.Errorf("MQTT connection failed: %v", err)
	}

	if os.Getenv("TUYA_MOBILE_WEBRTC") == "1" {
		ctx, cancel := context.WithTimeout(wb.ctx, 8*time.Second)
		mobile, mobileErr := tuya.NewMobileWakeClient(ctx, wb.user.SessionData.LoginResult)
		if mobileErr == nil {
			mobileErr = mobile.EnableMobileWebRTC(ctx)
		}
		cancel()
		if mobileErr != nil {
			if mobile != nil {
				mobile.Stop()
			}
			return mobileErr
		}
		wb.mqttClient.Stop()
		wb.mqttClient, wb.mobileWakeClient = mobile, mobile
		core.Logger.Info().Msg("Using mobile WebRTC control with local video routing")
	}

	// Get WebRTC configuration
	var webRTCConfig *tuya.WebRTCConfigResponse
	if os.Getenv("TUYA_MOBILE_WEBRTC") == "1" {
		helper := os.Getenv("TUYA_NATIVE_WAKE_HELPER")
		ctx, cancel := context.WithTimeout(wb.ctx, 15*time.Second)
		output, commandErr := exec.CommandContext(ctx, "python3", helper, "--device", wb.camera.DeviceID, "--webrtc-config").Output()
		cancel()
		if commandErr != nil {
			return errors.New("mobile WebRTC configuration unavailable")
		}
		webRTCConfig = &tuya.WebRTCConfigResponse{}
		err = json.Unmarshal(output, &webRTCConfig.Result)
	} else {
		webRTCConfig, err = tuya.GetWebRTCConfig(httpClient, wb.user.SessionData.ServerHost, wb.camera.DeviceID)
	}
	if err != nil {
		return fmt.Errorf("failed to get WebRTC config: %v", err)
	}

	skill, err := startupCameraSkill(webRTCConfig.Result.Skill, wb.camera.Skill)
	if err != nil {
		return err
	}

	if skill.LowPower != 0 {
		wakeContext, cancelWake := context.WithTimeout(wb.ctx, 45*time.Second)
		core.Logger.Info().Msg("Requesting low-power camera wake-up")
		nativeAnswered := false
		if helper := os.Getenv("TUYA_NATIVE_WAKE_HELPER"); helper != "" && os.Getenv("TUYA_MOBILE_WEBRTC") != "1" {
			args := []string{helper, "--device", wb.camera.DeviceID}
			if os.Getenv("TUYA_MOBILE_WEBRTC") == "1" {
				args = append(args, "--prepare-only")
			}
			command := exec.CommandContext(wakeContext, "python3", args...)
			if err := command.Run(); err != nil {
				core.Logger.Warn().Msg("Native wake did not answer; trying standard wake")
			} else {
				nativeAnswered = os.Getenv("TUYA_MOBILE_WEBRTC") != "1"
				core.Logger.Info().Msg("Native mobile wake answered; starting local video connection")
			}
		}
		if !nativeAnswered {
			wakeClient := wb.mqttClient
			if wb.mobileWakeClient == nil {
				wb.mobileWakeClient, err = tuya.NewMobileWakeClient(wakeContext, wb.user.SessionData.LoginResult)
			}
			if err == nil {
				wakeClient = wb.mobileWakeClient
				core.Logger.Info().Msg("Using mobile channel for camera wake-up")
			} else {
				core.Logger.Warn().Err(err).Msg("Mobile wake unavailable; using web wake channel")
			}
			startupWakeClient, startupLocalKey = wakeClient, webRTCConfig.Result.LocalKey
			wakeErr := wakeClient.RequestWakeUp(wakeContext, wb.camera.DeviceID, webRTCConfig.Result.LocalKey)
			if wakeErr != nil {
				core.Logger.Warn().Err(wakeErr).Msg("Camera wake-up request failed; trying direct connection")
			} else {
				core.Logger.Info().Msg("Camera wake command sent; requesting video immediately")
			}
		}
		cancelWake()
		if os.Getenv("TUYA_MOBILE_WEBRTC") != "1" {
			refreshed, refreshErr := tuya.GetWebRTCConfig(httpClient, wb.user.SessionData.ServerHost, wb.camera.DeviceID)
			if refreshErr != nil {
				return fmt.Errorf("failed to refresh connection after wake-up: %w", refreshErr)
			}
			webRTCConfig = refreshed
		}
	}

	// Determine stream settings
	wb.streamType = tuya.GetStreamType(&skill, wb.resolution)
	wb.isHEVC = tuya.IsHEVC(&skill, wb.streamType)

	core.Logger.Info().Msgf("Stream settings - Resolution: %s, Type: %d, HEVC: %v", wb.resolution, wb.streamType, wb.isHEVC)

	// Setup WebRTC peer connection
	if err := wb.setupPeerConnection(&webRTCConfig.Result); err != nil {
		return fmt.Errorf("failed to setup peer connection: %v", err)
	}

	if os.Getenv("TUYA_MOBILE_SIGNALING") == "1" && wb.mobileWakeClient != nil {
		signalContext, cancelSignal := context.WithTimeout(wb.ctx, 8*time.Second)
		err := wb.mobileWakeClient.EnableMobileSignaling(signalContext, wb.camera.DeviceID, webRTCConfig.Result.LocalKey)
		cancelSignal()
		if err != nil {
			return err
		}
		wb.mqttClient.Stop()
		wb.mqttClient = wb.mobileWakeClient
		core.Logger.Info().Msg("Using direct mobile MQTT signaling with local video routing")
	}

	// Setup MQTT camera client
	wb.setupMQTTCameraClient(&webRTCConfig.Result)

	// Create and send offer
	if err := wb.createAndSendOffer(); err != nil {
		return fmt.Errorf("failed to create offer: %v", err)
	}

	connectionTimeout := 15 * time.Second
	if skill.LowPower != 0 {
		connectionTimeout = 35 * time.Second
	}
	if err = wb.waitForConnectionRetry(connectionTimeout, 4*time.Second, func(attempt int) {
		if startupWakeClient != nil {
			ctx, cancel := context.WithTimeout(wb.ctx, 3*time.Second)
			err := startupWakeClient.RequestWakeUp(ctx, wb.camera.DeviceID, startupLocalKey)
			cancel()
			if err == nil {
				core.Logger.Info().Int("pulse", attempt).Msg("Camera startup wake pulse sent")
			}
		}
		if wb.peerConnection.RemoteDescription() == nil {
			if err := wb.cameraClient.ResendOffer(wb.offerSDP, wb.resolution, wb.offerStreamType(), wb.usesDataChannel(), attempt); err != nil {
				core.Logger.Warn().Msg("Camera startup offer retry failed")
			} else {
				core.Logger.Info().Int("retry", attempt).Msg("Camera startup offer retransmitted")
			}
		} else if wb.resolution == "hd" && !wb.usesDataChannel() && wb.peerConnection.ConnectionState() == pion.PeerConnectionStateConnected {
			_ = wb.cameraClient.SendResolution(0)
			core.Logger.Info().Msg("Camera startup HD command repeated")
		}
	}); err != nil {
		return fmt.Errorf("failed to establish connection: %v", err)
	}

	wb.connected = true
	core.Logger.Info().Msgf("WebRTC bridge started successfully for camera: %s", wb.camera.DeviceName)

	return nil
}

func (wb *WebRTCBridge) Stop() { wb.stop(true) }

func (wb *WebRTCBridge) stop(stopForwarder bool) {
	wb.cancel()
	wb.mutex.Lock()
	defer wb.mutex.Unlock()

	wb.connected = false
	if wb.activeAttempt != nil {
		wb.activeAttempt.stop(false)
	}

	core.Logger.Info().Msgf("Stopping WebRTC bridge for camera: %s", wb.camera.DeviceName)

	// Cancel context to stop all goroutines
	wb.cancel()

	// Send disconnect
	if wb.cameraClient != nil {
		wb.cameraClient.SendDisconnect()
	}

	// Close peer connection
	if wb.peerConnection != nil {
		wb.peerConnection.Close()
	}

	if wb.mobileWakeClient != nil && wb.mobileWakeClient != wb.mqttClient {
		wb.mobileWakeClient.Stop()
	}

	// Stop MQTT client
	if wb.mqttClient != nil {
		wb.mqttClient.Stop()
	}

	// Stop RTP forwarder
	if stopForwarder && wb.rtpForwarder != nil {
		wb.rtpForwarder.Stop()
	}

	core.Logger.Info().Msgf("WebRTC bridge stopped for camera: %s", wb.camera.DeviceName)
}

func (wb *WebRTCBridge) IsConnected() bool {
	wb.mutex.RLock()
	defer wb.mutex.RUnlock()
	return wb.connected
}

func (wb *WebRTCBridge) ForwardBackchannelAudioPacket(packet *rtp.Packet) {
	wb.mutex.RLock()
	child := wb.activeAttempt
	wb.mutex.RUnlock()
	if child != nil {
		child.ForwardBackchannelAudioPacket(packet)
		return
	}
	if wb.backchannel != nil {
		_ = wb.backchannel.WriteRTP(packet)
	}
}

func (wb *WebRTCBridge) setupPeerConnection(webRTCConfig *tuya.WebRTCConfig) error {
	// Convert ICE servers
	iceServerBytes, err := json.Marshal(webRTCConfig.P2PConfig.Ices)
	if err != nil {
		return fmt.Errorf("failed to marshal ICE servers: %v", err)
	}

	iceServers, err := webrtc.UnmarshalICEServers(iceServerBytes)
	if err != nil {
		return fmt.Errorf("failed to unmarshal ICE servers: %v", err)
	}

	// Create peer connection configuration
	if localMediaOnly() {
		iceServers = nil
	}
	conf := pion.Configuration{
		ICEServers:         iceServers,
		ICETransportPolicy: pion.ICETransportPolicyAll,
		BundlePolicy:       pion.BundlePolicyMaxBundle,
	}

	// Create WebRTC API
	api, err := webrtc.NewAPI()
	if err != nil {
		return fmt.Errorf("failed to create WebRTC API: %v", err)
	}

	// Create peer connection
	wb.peerConnection, err = api.NewPeerConnection(conf)
	if err != nil {
		return fmt.Errorf("failed to create peer connection: %v", err)
	}

	if wb.usesDataChannel() {
		wb.peerConnection.OnDataChannel(func(channel *pion.DataChannel) {
			core.Logger.Info().Str("label", channel.Label()).Msg("Remote HEVC data channel")
			wb.configureDataChannel(channel)
		})
		maxRetransmits := uint16(5)
		ordered := true
		channel, err := wb.peerConnection.CreateDataChannel("fmp4Stream", &pion.DataChannelInit{MaxRetransmits: &maxRetransmits, Ordered: &ordered})
		if err != nil {
			return err
		}
		wb.configureDataChannel(channel)
	}

	// Setup connection state handler
	wb.peerConnection.OnConnectionStateChange(func(state pion.PeerConnectionState) {
		if state == pion.PeerConnectionStateFailed || state == pion.PeerConnectionStateClosed {
			wb.handleError(errors.New("WebRTC connection failed/closed"))
		}

		if state == pion.PeerConnectionStateConnected {
			core.Logger.Info().Msgf("WebRTC connection established")

			if !wb.usesDataChannel() {
				if wb.resolution == "hd" {
					_ = wb.cameraClient.SendResolution(0)
				}
			}
		}
	})

	// Setup track handler for incoming media if not HEVC
	wb.peerConnection.OnTrack(func(track *pion.TrackRemote, receiver *pion.RTPReceiver) {
		codec := track.Codec()
		core.Logger.Trace().Msgf("Received track: %s, PayloadType: %d", codec.MimeType, codec.PayloadType)

		if track.Kind() == pion.RTPCodecTypeVideo {
			wb.videoTrack = track

			if !wb.usesDataChannel() {
				go wb.handleVideoTrack(track)
			}
		} else if track.Kind() == pion.RTPCodecTypeAudio {
			wb.audioTrack = track

			for _, tr := range wb.peerConnection.GetTransceivers() {
				if tr.Receiver() == receiver && tr.Kind() == pion.RTPCodecTypeAudio {
					if tr.Direction() == pion.RTPTransceiverDirectionSendrecv || tr.Direction() == pion.RTPTransceiverDirectionSendonly {
						localTrack, _ := pion.NewTrackLocalStaticRTP(
							pion.RTPCodecCapability{MimeType: track.Codec().MimeType},
							"audio-backchannel", "pion",
						)
						tr.Sender().ReplaceTrack(localTrack)
						wb.backchannel = localTrack
						core.Logger.Trace().Msgf("Setup backchannel track")
						break
					}
				}
			}

			if !wb.usesDataChannel() {
				go wb.handleAudioTrack(track)
			}
		}
	})

	return nil
}

func (wb *WebRTCBridge) setupMQTTCameraClient(webRTCConfig *tuya.WebRTCConfig) {
	device := &tuya.Device{
		DeviceId:   wb.camera.DeviceID,
		DeviceName: wb.camera.DeviceName,
		Category:   wb.camera.Category,
		ProductId:  wb.camera.ProductID,
		Uuid:       wb.camera.UUID,
	}

	// Create MQTT camera client
	wb.cameraClient = tuya.NewMqttCameraClient(wb.mqttClient, device, webRTCConfig)
	wb.mqttClient.AddCameraClient(wb.cameraClient.SessionId, wb.cameraClient)

	// Setup handlers
	wb.cameraClient.HandleAnswer = func(answer tuya.AnswerFrame) {
		if localMediaOnly() {
			answer.Sdp = localSDP(answer.Sdp)
		}
		core.Logger.Trace().Msgf("Received WebRTC answer")
		core.Logger.Trace().Msgf("Answer SDP: %s", answer.Sdp)

		desc := pion.SessionDescription{
			Type: pion.SDPTypePranswer,
			SDP:  answer.Sdp,
		}

		if err := wb.peerConnection.SetRemoteDescription(desc); err != nil {
			wb.handleError(err)
			return
		}

		if err := webrtc.SetAnswer(wb.peerConnection, answer.Sdp); err != nil {
			wb.handleError(err)
			return
		}
	}

	wb.cameraClient.HandleCandidate = func(candidate tuya.CandidateFrame) {
		candidateStr := strings.TrimSpace(candidate.Candidate)
		if localMediaOnly() && !localCandidate(candidateStr) {
			return
		}
		core.Logger.Trace().Msgf("Received ICE candidate: %s", candidateStr)

		if candidateStr != "" {
			// Remove "a=" prefix if present
			if strings.HasPrefix(candidateStr, "a=") {
				candidateStr = candidateStr[2:]
			}

			// Ensure candidate ends with CRLF
			if !strings.HasSuffix(candidateStr, "\r\n") && !strings.HasSuffix(candidateStr, "\n") {
				candidateStr = candidateStr + "\r\n"
			}

			core.Logger.Trace().Msgf("Adding ICE candidate: %s", strings.TrimSpace(candidateStr))

			if err := wb.peerConnection.AddICECandidate(pion.ICECandidateInit{
				Candidate: strings.TrimSpace(candidateStr),
			}); err != nil {
				wb.handleError(err)
			}
		}
	}

	wb.cameraClient.HandleError = func(err error) {
		wb.handleError(err)
	}

	wb.cameraClient.HandleDisconnect = func() {
		wb.handleError(errors.New("camera client disconnected"))
		wb.connected = false
	}
}

func (wb *WebRTCBridge) createAndSendOffer() error {
	wb.peerConnection.OnICECandidate(func(candidate *pion.ICECandidate) {
		if candidate != nil {
			if localMediaOnly() && !localCandidate("a="+candidate.ToJSON().Candidate) {
				return
			}
			core.Logger.Trace().Msgf("Generated ICE candidate: %s", candidate.ToJSON().Candidate)

			if err := wb.cameraClient.SendCandidate("a=" + candidate.ToJSON().Candidate); err != nil {
				core.Logger.Error().Err(err).Msg("Error sending ICE candidate")
			}
		}
	})

	medias := []*utils.Media{
		{Kind: utils.KindAudio, Direction: utils.DirectionSendRecv},
		{Kind: utils.KindVideo, Direction: utils.DirectionRecvonly},
	}

	// Create offer
	offer, err := webrtc.CreateOffer(wb.peerConnection, medias)
	if err != nil {
		return fmt.Errorf("failed to create offer: %v", err)
	}

	// Remove extmap lines to reduce payload size (device limitation)
	re := regexp.MustCompile(`\r\na=extmap[^\r\n]*`)
	offer = re.ReplaceAllString(offer, "")
	if localMediaOnly() {
		offer = localSDP(offer)
	}

	core.Logger.Trace().Msgf("Sending WebRTC offer")

	// Send offer
	wb.offerSDP = offer
	err = wb.cameraClient.SendOffer(offer, wb.resolution, wb.offerStreamType(), wb.usesDataChannel())
	if err != nil {
		return fmt.Errorf("failed to send offer: %w", err)
	}

	return nil
}

func (wb *WebRTCBridge) handleVideoTrack(track *pion.TrackRemote) {
	core.Logger.Trace().Msgf("Starting video track handler")
	var normalizer legacyHEVCPacketizer
	normalize := wb.isHEVC && !wb.usesDataChannel() && track.Codec().MimeType == pion.MimeTypeH264
	firstPacket := true

	for {
		select {
		case <-wb.ctx.Done():
			return
		default:
			packet, _, err := track.ReadRTP()
			if err != nil {
				if err == io.EOF {
					return
				}
				// Check if it's a known close error
				if strings.Contains(err.Error(), "closed") ||
					strings.Contains(err.Error(), "EOF") ||
					strings.Contains(err.Error(), "use of closed network connection") {
					return
				}
				core.Logger.Warn().Err(err).Msg("Unexpected error reading video RTP packet")
				time.Sleep(10 * time.Millisecond)
				continue
			}

			if normalize {
				packet = normalizer.convert(packet)
				if packet == nil || (firstPacket && !normalizer.parameters) {
					continue
				}
			}
			if firstPacket {
				wb.signalConnection(nil)
				if err := wb.rtpForwarder.WaitForPlay(wb.ctx); err != nil {
					return
				}
				firstPacket = false
			}
			wb.rtpForwarder.ForwardVideoPacket(packet)
		}
	}
}

func (wb *WebRTCBridge) handleAudioTrack(track *pion.TrackRemote) {
	core.Logger.Trace().Msgf("Starting audio track handler")

	for {
		select {
		case <-wb.ctx.Done():
			return
		default:
			packet, _, err := track.ReadRTP()
			if err != nil {
				if err == io.EOF {
					return
				}
				if strings.Contains(err.Error(), "closed") ||
					strings.Contains(err.Error(), "EOF") ||
					strings.Contains(err.Error(), "use of closed network connection") {
					return
				}
				core.Logger.Warn().Err(err).Msg("Unexpected error reading audio RTP packet")
				time.Sleep(10 * time.Millisecond)
				continue
			}

			wb.rtpForwarder.ForwardAudioPacket(packet)
		}
	}
}

func (wb *WebRTCBridge) createHTTPClient() *http.Client {
	jar, err := cookiejar.New(&cookiejar.Options{
		PublicSuffixList: publicsuffix.List,
	})
	if err != nil {
		return nil
	}

	if wb.user.SessionData != nil && len(wb.user.SessionData.Cookies) > 0 {
		serverURL, _ := url.Parse(fmt.Sprintf("https://%s", wb.user.SessionData.ServerHost))

		var httpCookies []*http.Cookie
		for _, cookie := range wb.user.SessionData.Cookies {
			httpCookies = append(httpCookies, &http.Cookie{
				Name:     cookie.Name,
				Value:    cookie.Value,
				Domain:   cookie.Domain,
				Path:     cookie.Path,
				Expires:  cookie.Expires,
				Secure:   cookie.Secure,
				HttpOnly: cookie.HttpOnly,
			})
		}

		jar.SetCookies(serverURL, httpCookies)
	}

	return &http.Client{
		Timeout: 30 * time.Second,
		Jar:     jar,
	}
}

func (wb *WebRTCBridge) probe(msg pion.DataChannelMessage) (bool, error) {
	var message tuya.DataChannelMessage
	if err := json.Unmarshal([]byte(msg.Data), &message); err != nil {
		return false, err
	}

	core.Logger.Info().Str("stage", message.Type).Msg("HEVC control message")
	switch message.Type {
	case "codec":
		frameRequest, _ := json.Marshal(tuya.DataChannelMessage{
			Type: "start",
			Msg:  "frame",
		})

		err := wb.sendMessageToDataChannel(frameRequest)
		if err != nil {
			return false, err
		}

	case "recv":
		core.Logger.Info().Str("metadata", message.Msg).Msg("HEVC routing metadata")
		var recvMessage tuya.RecvMessage
		if err := json.Unmarshal([]byte(message.Msg), &recvMessage); err != nil {
			return false, err
		}

		wb.rtpForwarder.videoSSRC = recvMessage.Video.SSRC
		wb.rtpForwarder.audioSSRC = recvMessage.Audio.SSRC

		completeMsg, _ := json.Marshal(tuya.DataChannelMessage{
			Type: "complete",
			Msg:  "",
		})

		err := wb.sendMessageToDataChannel(completeMsg)
		if err != nil {
			return false, err
		}

		return true, nil
	}

	return false, nil
}

func (wb *WebRTCBridge) sendMessageToDataChannel(message []byte) error {
	if wb.dataChannel != nil {
		return wb.dataChannel.Send(message)
	}

	return nil
}

func (wb *WebRTCBridge) handleError(err error) {
	wb.signalConnection(err)
	if wb.OnError != nil {
		wb.OnError(err)
	}
}

func (wb *WebRTCBridge) usesDataChannel() bool {
	return wb.isHEVC && os.Getenv("TUYA_HEVC_TRANSPORT") != "rtp"
}

func (wb *WebRTCBridge) configureDataChannel(channel *pion.DataChannel) {
	wb.dataChannel = channel
	receivedFirstMessage := false
	receivedFirstPacket := false
	channel.OnMessage(func(msg pion.DataChannelMessage) {
		if !receivedFirstMessage {
			receivedFirstMessage = true
			core.Logger.Info().Bool("text", msg.IsString).Int("bytes", len(msg.Data)).Msg("First HEVC data message")
		}
		if msg.IsString || json.Valid(msg.Data) {
			if connected, err := wb.probe(msg); err != nil {
				wb.handleError(err)
			} else if connected {
				wb.signalConnection(nil)
			}
		} else {
			packet := &rtp.Packet{}
			if err := packet.Unmarshal(msg.Data); err != nil {
				if !receivedFirstPacket {
					core.Logger.Warn().Err(err).Int("bytes", len(msg.Data)).Msg("HEVC packet parse failed")
				}
				receivedFirstPacket = true
				return
			}
			if !receivedFirstPacket {
				receivedFirstPacket = true
				core.Logger.Info().Uint32("ssrc", packet.SSRC).Uint8("payload_type", packet.PayloadType).Uint32("expected_video", wb.rtpForwarder.videoSSRC).Uint32("expected_audio", wb.rtpForwarder.audioSSRC).Msg("First HEVC RTP packet")
			}

			switch packet.SSRC {
			case wb.rtpForwarder.videoSSRC:
				wb.rtpForwarder.ForwardVideoPacket(packet)
			case wb.rtpForwarder.audioSSRC:
				wb.rtpForwarder.ForwardAudioPacket(packet)
			}
		}
	})

	channel.OnError(func(err error) {
		wb.handleError(err)
	})

	channel.OnClose(func() {
		wb.handleError(errors.New("datachannel: closed"))
	})

	channel.OnOpen(func() {
		core.Logger.Info().Msg("HEVC data channel opened")
		codecRequest, _ := json.Marshal(tuya.DataChannelMessage{
			Type: "codec",
			Msg:  "",
		})

		if err := wb.sendMessageToDataChannel(codecRequest); err != nil {
			wb.handleError(fmt.Errorf("failed to send codec request: %w", err))
		}
		if os.Getenv("TUYA_HEVC_EAGER_START") == "1" {
			frameRequest, _ := json.Marshal(tuya.DataChannelMessage{Type: "start", Msg: "frame"})
			if err := wb.sendMessageToDataChannel(frameRequest); err != nil {
				wb.handleError(err)
			}
		}

	})
}

func (wb *WebRTCBridge) offerStreamType() int {
	if wb.isHEVC && !wb.usesDataChannel() {
		return 1
	}
	return wb.streamType
}
