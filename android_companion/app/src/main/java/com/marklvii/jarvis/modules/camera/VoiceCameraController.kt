package com.marklvii.jarvis.modules.camera

import android.content.Context
import androidx.camera.core.Camera
import androidx.camera.core.CameraSelector
import androidx.camera.core.ImageCapture
import androidx.camera.core.ImageCaptureException
import androidx.camera.lifecycle.ProcessCameraProvider
import androidx.camera.video.FileOutputOptions
import androidx.camera.video.Quality
import androidx.camera.video.QualitySelector
import androidx.camera.video.Recorder
import androidx.camera.video.Recording
import androidx.camera.video.VideoCapture
import androidx.camera.video.VideoRecordEvent
import androidx.core.content.ContextCompat
import androidx.lifecycle.LifecycleOwner
import java.io.File

/**
 * 7/7 Android MAX: CameraX Voice Zoom, Hands-Free Selfie/Rear Capture & Video Recording.
 */
class VoiceCameraController(private val context: Context) {
    private var boundCamera: Camera? = null
    private var imageCapture: ImageCapture? = null
    private var videoCapture: VideoCapture<Recorder>? = null
    private var activeRecording: Recording? = null

    fun bind(lifecycleOwner: LifecycleOwner, frontCamera: Boolean = false) {
        val future = ProcessCameraProvider.getInstance(context)
        future.addListener({
            val provider = future.get()
            val selector = if (frontCamera) {
                CameraSelector.DEFAULT_FRONT_CAMERA
            } else {
                CameraSelector.DEFAULT_BACK_CAMERA
            }
            val imgCap = ImageCapture.Builder().build()
            val recorder = Recorder.Builder()
                .setQualitySelector(QualitySelector.from(Quality.HD))
                .build()
            val vidCap = VideoCapture.withOutput(recorder)
            provider.unbindAll()
            boundCamera = provider.bindToLifecycle(lifecycleOwner, selector, imgCap, vidCap)
            imageCapture = imgCap
            videoCapture = vidCap
        }, ContextCompat.getMainExecutor(context))
    }

    fun setZoomRatio(ratio: Float): Float {
        val clamped = ratio.coerceIn(1.0f, 8.0f)
        boundCamera?.cameraControl?.setZoomRatio(clamped)
        return clamped
    }

    fun capturePhoto(fileNamePrefix: String = "jarvis_cam", onSaved: (Result<File>) -> Unit) {
        val imgCap = imageCapture ?: run {
            onSaved(Result.failure(IllegalStateException("Camera not bound")))
            return
        }
        val out = File(context.filesDir, "${fileNamePrefix}_${System.currentTimeMillis()}.jpg")
        val opts = ImageCapture.OutputFileOptions.Builder(out).build()
        imgCap.takePicture(
            opts,
            ContextCompat.getMainExecutor(context),
            object : ImageCapture.OnImageSavedCallback {
                override fun onImageSaved(output: ImageCapture.OutputFileResults) {
                    onSaved(Result.success(out))
                }
                override fun onError(exception: ImageCaptureException) {
                    onSaved(Result.failure(exception))
                }
            }
        )
    }

    fun toggleVideoRecording(onStopped: (String) -> Unit): Boolean {
        activeRecording?.let { rec ->
            rec.stop()
            activeRecording = null
            onStopped("Stopped recording")
            return false
        }
        val vidCap = videoCapture ?: return false
        val out = File(context.filesDir, "jarvis_vid_${System.currentTimeMillis()}.mp4")
        val pending = vidCap.output.prepareRecording(context, FileOutputOptions.Builder(out).build())
        activeRecording = pending.start(ContextCompat.getMainExecutor(context)) { ev ->
            if (ev is VideoRecordEvent.Finalize) {
                onStopped("Saved video: ${out.name}")
            }
        }
        return true
    }
}
