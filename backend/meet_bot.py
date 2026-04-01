"""Google Meet bot — joins a meeting and captures audio for transcription."""
import asyncio
import logging
import os
import tempfile
from typing import Callable, Optional

logger = logging.getLogger(__name__)


async def join_meet(
    meet_url: str,
    bot_name: str = "AI Recorder",
    on_audio_chunk: Optional[Callable] = None,
    stop_event: Optional[asyncio.Event] = None,
    chunk_duration_ms: int = 10000,
) -> None:
    """
    Join a Google Meet and capture audio.

    Args:
        meet_url: The Google Meet URL (e.g., https://meet.google.com/abc-defg-hij)
        bot_name: Name shown in the meeting
        on_audio_chunk: Async callback(audio_bytes) called every chunk_duration_ms
        stop_event: Set this event to leave the meeting
        chunk_duration_ms: Audio chunk duration in milliseconds
    """
    from playwright.async_api import async_playwright

    if stop_event is None:
        stop_event = asyncio.Event()

    async with async_playwright() as p:
        # Launch Chromium - use default Playwright headless
        browser = await p.chromium.launch(
            headless=True,
            args=[
                '--use-fake-ui-for-media-stream',
                '--use-fake-device-for-media-stream',
                '--auto-accept-camera-and-microphone-capture',
                '--autoplay-policy=no-user-gesture-required',
                '--no-sandbox',
                '--disable-setuid-sandbox',
                '--disable-gpu',
                '--disable-dev-shm-usage',
            ],
        )
        logger.info("Chromium browser launched")

        context = await browser.new_context(
            permissions=['microphone', 'camera'],
            user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        )

        page = await context.new_page()
        logger.info(f"Browser ready, navigating to {meet_url}")

        try:
            # Navigate to Meet
            await page.goto(meet_url, wait_until='networkidle', timeout=30000)
            await asyncio.sleep(3)

            # Turn off camera and mic before joining
            # Google Meet pre-join screen: find and click mic/camera toggle buttons
            try:
                # Try to turn off camera
                cam_btn = page.locator('[aria-label*="camera" i], [aria-label*="Camera" i], [data-tooltip*="camera" i]').first
                if await cam_btn.count() > 0:
                    await cam_btn.click()
                    logger.info("Camera turned off")
            except Exception:
                pass

            try:
                # Try to turn off mic
                mic_btn = page.locator('[aria-label*="microphone" i], [aria-label*="Microphone" i], [data-tooltip*="microphone" i]').first
                if await mic_btn.count() > 0:
                    await mic_btn.click()
                    logger.info("Mic turned off")
            except Exception:
                pass

            await asyncio.sleep(1)

            # Enter name if there's a name input (for non-logged-in users)
            try:
                name_input = page.locator('input[aria-label*="name" i], input[placeholder*="name" i]').first
                if await name_input.count() > 0:
                    await name_input.fill(bot_name)
                    logger.info(f"Name set to: {bot_name}")
            except Exception:
                pass

            await asyncio.sleep(1)

            # Click "Ask to join" or "Join now" button
            joined = False
            join_selectors = [
                'button:has-text("Ask to join")',
                'button:has-text("Join now")',
                'button:has-text("Join")',
                '[data-tooltip*="Join" i]',
                'button[jsname="Qx7uuf"]',
            ]
            for sel in join_selectors:
                try:
                    btn = page.locator(sel).first
                    if await btn.count() > 0 and await btn.is_visible():
                        await btn.click()
                        joined = True
                        logger.info(f"Clicked join button: {sel}")
                        break
                except Exception:
                    continue

            if not joined:
                logger.error("Could not find join button")
                # Take screenshot for debugging
                screenshot_path = '/tmp/meet_debug.png'
                await page.screenshot(path=screenshot_path)
                logger.error(f"Debug screenshot saved to {screenshot_path}")
                return

            # Wait for meeting to load
            logger.info("Waiting to be admitted to meeting...")
            await asyncio.sleep(10)

            # Inject audio capture script
            # This captures all audio playing in the page (other participants)
            logger.info("Setting up audio capture...")
            await page.evaluate(f"""() => {{
                window._audioChunks = [];
                window._isCapturing = true;
                window._chunkDuration = {chunk_duration_ms};

                // Intercept AudioContext to capture meeting audio
                const OriginalAudioContext = window.AudioContext || window.webkitAudioContext;
                const audioCtx = new OriginalAudioContext();
                const dest = audioCtx.createMediaStreamDestination();

                // Hook into all audio elements on the page
                function captureAudioElements() {{
                    document.querySelectorAll('audio, video').forEach(el => {{
                        if (!el._captured) {{
                            try {{
                                const source = audioCtx.createMediaElementSource(el);
                                source.connect(dest);
                                source.connect(audioCtx.destination); // Still play audio
                                el._captured = true;
                                console.log('Captured audio element:', el.tagName);
                            }} catch(e) {{}}
                        }}
                    }});
                }}

                // Also try to capture via MediaStream (WebRTC audio tracks)
                const origAddTrack = RTCPeerConnection.prototype.addTrack;
                const origOnTrack = Object.getOwnPropertyDescriptor(RTCPeerConnection.prototype, 'ontrack');

                // Monitor for incoming audio tracks
                const peerConnections = [];
                const OrigRTC = window.RTCPeerConnection;
                window.RTCPeerConnection = function(...args) {{
                    const pc = new OrigRTC(...args);
                    peerConnections.push(pc);
                    pc.addEventListener('track', (event) => {{
                        if (event.track.kind === 'audio') {{
                            try {{
                                const stream = new MediaStream([event.track]);
                                const source = audioCtx.createMediaStreamSource(stream);
                                source.connect(dest);
                                console.log('Captured WebRTC audio track');
                            }} catch(e) {{ console.error('Failed to capture track:', e); }}
                        }}
                    }});
                    return pc;
                }};
                window.RTCPeerConnection.prototype = OrigRTC.prototype;

                // Set up MediaRecorder on the captured audio
                const recorder = new MediaRecorder(dest.stream, {{
                    mimeType: 'audio/webm;codecs=opus',
                    audioBitsPerSecond: 128000
                }});

                window._meetRecorder = recorder;
                window._recordedParts = [];

                recorder.ondataavailable = (e) => {{
                    if (e.data.size > 0) {{
                        window._recordedParts.push(e.data);
                    }}
                }};

                // Record in chunks
                recorder.start({chunk_duration_ms});

                // Periodically check for new audio elements
                setInterval(captureAudioElements, 2000);

                console.log('Audio capture initialized');
            }}""")

            logger.info("Audio capture started, listening to meeting...")

            # Poll for audio chunks and send them
            while not stop_event.is_set():
                await asyncio.sleep(chunk_duration_ms / 1000 + 1)

                if stop_event.is_set():
                    break

                # Get recorded audio from the page
                try:
                    has_data = await page.evaluate("""() => {
                        return window._recordedParts && window._recordedParts.length > 0;
                    }""")

                    if has_data and on_audio_chunk:
                        # Extract audio as base64
                        audio_b64 = await page.evaluate("""() => {
                            return new Promise((resolve) => {
                                const blob = new Blob(window._recordedParts, {type: 'audio/webm'});
                                window._recordedParts = [];
                                const reader = new FileReader();
                                reader.onloadend = () => resolve(reader.result.split(',')[1]);
                                reader.readAsDataURL(blob);
                            });
                        }""")

                        if audio_b64:
                            import base64
                            audio_bytes = base64.b64decode(audio_b64)
                            logger.info(f"Got audio chunk: {len(audio_bytes)} bytes")
                            if len(audio_bytes) > 1000:  # Skip tiny/empty chunks
                                await on_audio_chunk(audio_bytes)

                except Exception as e:
                    logger.warning(f"Audio capture error: {e}")

        except Exception as e:
            logger.error(f"Meet bot error: {e}")
            try:
                await page.screenshot(path='/tmp/meet_error.png')
            except Exception:
                pass
        finally:
            logger.info("Leaving meeting...")
            try:
                # Click leave button
                leave_btn = page.locator('[aria-label*="Leave" i], button:has-text("Leave")').first
                if await leave_btn.count() > 0:
                    await leave_btn.click()
            except Exception:
                pass
            await browser.close()
            logger.info("Meet bot disconnected")
