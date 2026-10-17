package rtsp

import (
	"bufio"
	"context"
	"errors"
	"net"
	"strings"
	"testing"
	"time"
	"tuya-ipc-terminal/pkg/storage"
)

func TestPlayWaitsForCameraWakeBeforeAcknowledging(t *testing.T) {
	bridge := NewWebRTCBridge(&storage.CameraInfo{}, "hd", nil, nil)
	defer bridge.Stop()
	serverConn, playerConn := net.Pipe()
	defer serverConn.Close()
	defer playerConn.Close()
	client := &RTSPClient{conn: serverConn, session: "test-session", stream: &CameraStream{webrtcBridge: bridge}}
	done := make(chan struct{})
	go func() {
		(&RTSPServer{}).handlePlay(client, &RTSPRequest{Headers: map[string]string{"Session": "test-session"}, CSeq: 5, URL: "rtsp://localhost/camera/hd"})
		close(done)
	}()

	playerConn.SetReadDeadline(time.Now().Add(30 * time.Millisecond))
	if _, err := bufio.NewReader(playerConn).ReadString('\n'); err == nil {
		t.Fatal("PLAY acknowledged before camera was ready")
	}
	bridge.finishWake(nil)
	playerConn.SetReadDeadline(time.Now().Add(time.Second))
	reader := bufio.NewReader(playerConn)
	line, err := reader.ReadString('\n')
	if err != nil || !strings.Contains(line, "200 OK") {
		t.Fatalf("missing PLAY acknowledgement: %q %v", line, err)
	}
	for {
		line, err = reader.ReadString('\n')
		if err != nil {
			t.Fatal(err)
		}
		if line == "\r\n" {
			break
		}
	}
	<-done
}

func TestWakeWaitPropagatesFailureAndCancellation(t *testing.T) {
	bridge := NewWebRTCBridge(&storage.CameraInfo{}, "hd", nil, nil)
	failure := errors.New("wake failed")
	bridge.finishWake(failure)
	bridge.finishWake(nil)
	if !errors.Is(bridge.waitForWake(context.Background()), failure) {
		t.Fatal("wake error lost")
	}
	other := NewWebRTCBridge(&storage.CameraInfo{}, "hd", nil, nil)
	other.Stop()
	if !errors.Is(other.waitForWake(context.Background()), context.Canceled) {
		t.Fatal("cancellation ignored")
	}
	bridge.Stop()
}

func TestUnansweredConnectionTimesOutAndStopCancelsPendingStart(t *testing.T) {
	bridge := NewWebRTCBridge(&storage.CameraInfo{}, "hd", nil, nil)
	if err := bridge.waitForConnection(20 * time.Millisecond); err == nil {
		t.Fatal("unanswered camera request waited forever")
	}
	bridge.Stop()
	other := NewWebRTCBridge(&storage.CameraInfo{}, "hd", nil, nil)
	started := make(chan struct{})
	done := make(chan error, 1)
	go func() {
		other.mutex.Lock()
		defer other.mutex.Unlock()
		close(started)
		done <- other.waitForConnection(time.Minute)
	}()
	<-started
	other.Stop()
	select {
	case err := <-done:
		if !errors.Is(err, context.Canceled) {
			t.Fatal("stop did not cancel startup")
		}
	case <-time.After(time.Second):
		t.Fatal("stop deadlocked with startup")
	}
}

func TestFirstConnectionOutcomeWins(t *testing.T) {
	bridge := NewWebRTCBridge(&storage.CameraInfo{}, "hd", nil, nil)
	defer bridge.Stop()
	failure := errors.New("camera refused")
	bridge.signalConnection(failure)
	bridge.signalConnection(nil)
	if !errors.Is(bridge.waitForConnection(time.Second), failure) {
		t.Fatal("late event replaced the original connection result")
	}
}

func TestJoiningConnectingStreamDoesNotStartSecondCameraSession(t *testing.T) {
	stream := &CameraStream{connecting: true, startRequested: true, clients: make(map[string]*RTSPClient)}
	// There is deliberately no bridge: a duplicate start would panic.
	stream.AddClient(&RTSPClient{session: "one"})
	stream.AddClient(&RTSPClient{session: "two"})
	if len(stream.clients) != 2 || !stream.startRequested {
		t.Fatal("joining readers lost")
	}
}

func TestCameraReturnsToSleepWhenReaderLeavesDuringWake(t *testing.T) {
	stream := NewCameraStream(&storage.CameraInfo{}, "hd", nil, nil, nil)
	stream.SetShutdownDelay(time.Hour)
	defer stream.Stop()
	stream.connecting = true
	stream.webrtcBridge.connected = true
	stream.finishStart()
	stream.mutex.Lock()
	scheduled := stream.shutdownTimer != nil
	stream.mutex.Unlock()
	if !scheduled {
		t.Fatal("camera left awake after reader disconnected during wake")
	}
}

func TestCameraStartupRetriesUntilThirdAttemptWithoutAcknowledgingEarly(t *testing.T) {
	bridge := NewWebRTCBridge(&storage.CameraInfo{}, "hd", nil, nil)
	defer bridge.Stop()
	calls := 0
	err := retryCameraStart(context.Background(), 3, time.Millisecond, func(attempt int) error {
		calls++
		select {
		case <-bridge.wakeReady:
			t.Fatal("player released before video arrived")
		default:
		}
		if attempt < 3 {
			return errors.New("no video")
		}
		return nil
	})
	bridge.finishWake(err)
	if calls != 3 || bridge.waitForWake(context.Background()) != nil {
		t.Fatalf("third attempt did not succeed: %d %v", calls, err)
	}
}

func TestCameraStartupRetriesAreBoundedAndCancelable(t *testing.T) {
	calls := 0
	err := retryCameraStart(context.Background(), 3, 0, func(int) error { calls++; return errors.New("no video") })
	if err == nil || calls != 3 {
		t.Fatalf("unbounded retry: %d %v", calls, err)
	}
	ctx, cancel := context.WithCancel(context.Background())
	calls = 0
	err = retryCameraStart(ctx, 3, time.Hour, func(int) error { calls++; cancel(); return errors.New("no video") })
	if !errors.Is(err, context.Canceled) || calls != 1 {
		t.Fatalf("cancellation ignored: %d %v", calls, err)
	}
	calls = 0
	err = retryCameraStart(context.Background(), 3, 0, func(int) error { calls++; return nil })
	if err != nil || calls != 1 {
		t.Fatalf("successful startup retried: %d %v", calls, err)
	}
}

func TestFailedAttemptPreservesPlayerAndFinalStopClosesIt(t *testing.T) {
	parent := NewWebRTCBridge(&storage.CameraInfo{}, "hd", nil, nil)
	child := NewWebRTCBridge(&storage.CameraInfo{}, "hd", nil, nil)
	child.cancel()
	child.ctx, child.cancel = context.WithCancel(parent.ctx)
	child.rtpForwarder = parent.rtpForwarder
	parent.activeAttempt = child
	server, player := net.Pipe()
	defer server.Close()
	defer player.Close()
	if err := parent.rtpForwarder.AddTCPClient("waiting-player", server, 0, 2, 4); err != nil {
		t.Fatal(err)
	}
	child.stop(false)
	if len(parent.rtpForwarder.clients) != 1 {
		t.Fatal("failed attempt removed waiting VLC client")
	}
	parent.Stop()
	if len(parent.rtpForwarder.clients) != 0 {
		t.Fatal("final stop did not release VLC client")
	}
}

func TestStartupRetriesInSameSessionUntilActualVideoArrives(t *testing.T) {
	bridge := NewWebRTCBridge(&storage.CameraInfo{}, "hd", nil, nil)
	defer bridge.Stop()
	pulses := 0
	err := bridge.waitForConnectionRetry(time.Second, time.Millisecond, func(attempt int) {
		pulses++
		if attempt == 3 {
			bridge.signalConnection(nil)
		}
	})
	if err != nil || pulses != 3 {
		t.Fatalf("startup retry failed: %d %v", pulses, err)
	}
	time.Sleep(4 * time.Millisecond)
	if pulses != 3 {
		t.Fatal("wake pulses continued after video arrived")
	}
}

func TestStartupWakePulsesStopOnCancellationOrDeadline(t *testing.T) {
	for _, cancelOnPulse := range []bool{false, true} {
		bridge := NewWebRTCBridge(&storage.CameraInfo{}, "hd", nil, nil)
		pulses := 0
		err := bridge.waitForConnectionRetry(8*time.Millisecond, time.Millisecond, func(int) {
			pulses++
			if cancelOnPulse {
				bridge.cancel()
			}
		})
		bridge.Stop()
		if err == nil || pulses == 0 {
			t.Fatal("startup was not bounded")
		}
		if cancelOnPulse && (pulses != 1 || !errors.Is(err, context.Canceled)) {
			t.Fatal("cancellation ignored")
		}
	}
}

func TestBrokenLiveSessionCannotRemainMarkedConnected(t *testing.T) {
	bridge := NewWebRTCBridge(&storage.CameraInfo{}, "hd", nil, nil)
	defer bridge.Stop()
	bridge.connected = true
	calls := 0
	bridge.OnError = func(error) { calls++ }
	bridge.connectionFailed(errors.New("camera closed session"))
	bridge.connectionFailed(errors.New("late peer close"))
	if bridge.IsConnected() || calls != 1 {
		t.Fatal("broken session remained reusable")
	}
}

func TestSessionClosedBeforeStartupFinishesCannotBecomeActive(t *testing.T) {
	stream := NewCameraStream(&storage.CameraInfo{}, "hd", nil, nil, nil)
	stream.connecting = true
	stream.startRequested = true
	stream.webrtcBridge.Stop()
	stream.finishStart()
	if stream.active || stream.connecting || stream.startRequested {
		t.Fatal("closed session was revived after startup")
	}
}

func TestIncompleteLiveCapabilitiesCannotDisableBatteryCameraWake(t *testing.T) {
	live := `{"videos":[{"streamType":2}]}`
	cached := `{"lowPower":1,"videos":[{"streamType":4}],"audios":[{"codecType":105}]}`
	skill, err := startupCameraSkill(live, cached)
	if err != nil || skill.LowPower != 1 || len(skill.Audios) != 1 || skill.Videos[0].StreamType != 2 {
		t.Fatalf("lost battery capability or changed live video: %+v %v", skill, err)
	}
	skill, err = startupCameraSkill(`{"lowPower":1}`, cached)
	if err != nil || skill.Videos[0].StreamType != 4 {
		t.Fatal("cached video capability lost")
	}
	if _, err = startupCameraSkill(live, `bad`); err != nil {
		t.Fatal("valid live capabilities rejected")
	}
	if _, err = startupCameraSkill(`bad`, cached); err == nil {
		t.Fatal("malformed live capability accepted")
	}
}
