/*
 * Drama Llama spatial renderer — thin C wrapper around SAF's ambi_roomsim.
 *
 * Exposes a flat, ctypes-friendly API (dl_*). Everything is real-time-rate
 * processing driven by the Python mixer, one scene at a time:
 *
 *   dl_create(cfg)            -> handle
 *   dl_set_sources(h, n, xyz) -> set source positions for the frame
 *   dl_process(h, in, out)    -> in: nSources x nSamples, out: nSH x nSamples
 *   dl_destroy(h)
 *
 * SAF is ISC-licensed (see third_party/Spatial_Audio_Framework/LICENSE.md);
 * the AmbiRoomSim *plugin* (GPLv3) is deliberately not used or copied here —
 * only the library algorithm it wraps.
 */

#include <stdlib.h>
#include <string.h>
#include <math.h>

#include "ambi_roomsim.h"
#include "saf.h"

/* ------------------------------------------------------------------ limits */

#define DL_MAX_SOURCES 16
#define DL_MAX_ORDER 3
#define DL_MAX_SH ((DL_MAX_ORDER + 1) * (DL_MAX_ORDER + 1))

/* SAF's room sim runs on a fixed internal frame size (128 by default). We
 * buffer into that. */
#if !defined(DL_FRAME_SIZE)
#define DL_FRAME_SIZE ambi_roomsim_getFrameSize()
#endif

#define DL_EXPORT __attribute__((visibility("default")))

/* ------------------------------------------------------------------- state */

typedef struct {
    void *hAmbi;

    int frame_size;
    int n_sources;
    int order;
    int n_sh;

    int sample_rate;

    /* Room geometry, metres. */
    float room[3];
    float absorption[6];
    int reflection_order;
    int enable_reflections;

    /* Listener pose. */
    float listener[3];
    float listener_facing_deg; /* 0 = front wall (-y), +90 = +x */

    /* Sources, metres + per-source gain. */
    float src_pos[DL_MAX_SOURCES][3];
    float src_gain[DL_MAX_SOURCES];

    /* Streaming buffers. */
    float *in_frames;  /* DL_MAX_SOURCES * frame_size */
    float *sh_frames;  /* DL_MAX_SH * frame_size */
    float *rot_mtx;    /* n_sh * n_sh */
    int rot_order;

    int prepared;

    char error[256];
} dl_handle_t;

/* ------------------------------------------------------------ error helper */

static void dl_set_error(dl_handle_t *h, const char *msg)
{
    if (!h) return;
    strncpy(h->error, msg, sizeof(h->error) - 1);
    h->error[sizeof(h->error) - 1] = '\0';
}

DL_EXPORT const char *dl_last_error(void *handle)
{
    dl_handle_t *h = (dl_handle_t *)handle;
    if (!h) return "";
    return h->error;
}

/* Forward declaration: dl_create's OOM path tears down a partially built
   handle, and dl_destroy is defined below. */
DL_EXPORT void dl_destroy(void *handle);

/* --------------------------------------------------------------- rotation */

/* Rotation matrix for a listener yaw about +z.
 *
 * SAF's Ambisonic convention is +x = front, +y = left. The stage UI uses a
 * standard azimuth where 0 faces the front wall (-y) and +90 faces +x, so a
 * listener facing az must be rotated by (90 - az) to land on the decoder's
 * front axis. This mirrors SPARTA's receiver-facing rotation.
 */
static void dl_build_yaw_matrix(float deg, float R[3][3])
{
    const float rad = (90.0f - deg) * (float)M_PI / 180.0f;
    const float c = cosf(rad);
    const float s = sinf(rad);

    /* Rotation about z by `rad`. */
    R[0][0] = c;   R[0][1] = -s;  R[0][2] = 0.0f;
    R[1][0] = s;   R[1][1] =  c;  R[1][2] = 0.0f;
    R[2][0] = 0.0f; R[2][1] = 0.0f; R[2][2] = 1.0f;
}

static void dl_update_rotation(dl_handle_t *h)
{
    float R[3][3];

    if (!h || h->rot_order == h->order) return;

    dl_build_yaw_matrix(h->listener_facing_deg, R);
    getSHrotMtxReal(R, h->rot_mtx, h->order);

    h->rot_order = h->order;
}

/* ------------------------------------------------------------ lifecycle */

DL_EXPORT void *dl_create(int sample_rate, int order, int reflection_order,
                          int enable_reflections, const float *room_dims,
                          const float *absorption)
{
    dl_handle_t *h = (dl_handle_t *)calloc(1, sizeof(dl_handle_t));
    if (!h) return NULL;

    h->sample_rate = (sample_rate > 0) ? sample_rate : 48000;
    h->order = (order < 1) ? 1 : (order > DL_MAX_ORDER ? DL_MAX_ORDER : order);
    h->n_sh = (h->order + 1) * (h->order + 1);
    h->reflection_order = reflection_order;
    h->enable_reflections = enable_reflections ? 1 : 0;
    h->rot_order = -1; /* force first rotation build */

    for (int i = 0; i < 3; i++)
        h->room[i] = room_dims ? room_dims[i] : 6.0f;
    for (int i = 0; i < 6; i++)
        h->absorption[i] = absorption ? absorption[i] : 0.25f;

    for (int i = 0; i < DL_MAX_SOURCES; i++) {
        for (int k = 0; k < 3; k++) h->src_pos[i][k] = 0.0f;
        h->src_gain[i] = 1.0f;
    }

    ambi_roomsim_create(&h->hAmbi);
    if (!h->hAmbi) {
        free(h);
        return NULL;
    }

    ambi_roomsim_init(h->hAmbi, h->sample_rate);

    h->frame_size = ambi_roomsim_getFrameSize();
    if (h->frame_size <= 0) h->frame_size = 128;

    h->in_frames = (float *)calloc((size_t)DL_MAX_SOURCES * h->frame_size, sizeof(float));
    h->sh_frames = (float *)calloc((size_t)DL_MAX_SH * h->frame_size, sizeof(float));
    h->rot_mtx   = (float *)calloc((size_t)DL_MAX_SH * DL_MAX_SH, sizeof(float));

    if (!h->in_frames || !h->sh_frames || !h->rot_mtx) {
        dl_destroy(h);
        return NULL;
    }

    /* Push configuration into the DSP. */
    ambi_roomsim_setOutputOrder(h->hAmbi, (SH_ORDERS)h->order);
    ambi_roomsim_setChOrder(h->hAmbi, CH_ACN);
    ambi_roomsim_setNormType(h->hAmbi, NORM_N3D);
    ambi_roomsim_setMaxReflectionOrder(h->hAmbi, h->reflection_order);
    ambi_roomsim_setEnableIMSflag(h->hAmbi, h->enable_reflections);

    ambi_roomsim_setRoomDimX(h->hAmbi, h->room[0]);
    ambi_roomsim_setRoomDimY(h->hAmbi, h->room[1]);
    ambi_roomsim_setRoomDimZ(h->hAmbi, h->room[2]);
    for (int i = 0; i < 6; i++) {
        int xyz = i / 2;
        int posneg = i % 2;
        ambi_roomsim_setWallAbsCoeff(h->hAmbi, xyz, posneg, h->absorption[i]);
    }

    ambi_roomsim_setNumReceivers(h->hAmbi, 1);
    ambi_roomsim_setReceiverX(h->hAmbi, 0, h->listener[0]);
    ambi_roomsim_setReceiverY(h->hAmbi, 0, h->listener[1]);
    ambi_roomsim_setReceiverZ(h->hAmbi, 0, h->listener[2]);

    h->prepared = 1;
    return h;
}

DL_EXPORT void dl_destroy(void *handle)
{
    dl_handle_t *h = (dl_handle_t *)handle;
    if (!h) return;

    if (h->hAmbi) ambi_roomsim_destroy(&h->hAmbi);

    free(h->in_frames);
    free(h->sh_frames);
    free(h->rot_mtx);
    free(h);
}

/* ---------------------------------------------------------- configuration */

DL_EXPORT int dl_set_room(void *handle, const float *dims, const float *absorption)
{
    dl_handle_t *h = (dl_handle_t *)handle;
    if (!h || !dims) return -1;

    h->room[0] = dims[0];
    h->room[1] = dims[1];
    h->room[2] = dims[2];

    if (absorption) {
        for (int i = 0; i < 6; i++)
            h->absorption[i] = absorption[i];
    }

    ambi_roomsim_setRoomDimX(h->hAmbi, h->room[0]);
    ambi_roomsim_setRoomDimY(h->hAmbi, h->room[1]);
    ambi_roomsim_setRoomDimZ(h->hAmbi, h->room[2]);
    for (int i = 0; i < 6; i++) {
        ambi_roomsim_setWallAbsCoeff(h->hAmbi, i / 2, i % 2, h->absorption[i]);
    }

    /* Geometry changed: the echograms must be rebuilt. */
    ambi_roomsim_refreshParams(h->hAmbi);
    return 0;
}

DL_EXPORT int dl_set_listener(void *handle, const float *pos, float facing_deg)
{
    dl_handle_t *h = (dl_handle_t *)handle;
    if (!h || !pos) return -1;

    h->listener[0] = pos[0];
    h->listener[1] = pos[1];
    h->listener[2] = pos[2];
    h->listener_facing_deg = facing_deg;

    ambi_roomsim_setReceiverX(h->hAmbi, 0, h->listener[0]);
    ambi_roomsim_setReceiverY(h->hAmbi, 0, h->listener[1]);
    ambi_roomsim_setReceiverZ(h->hAmbi, 0, h->listener[2]);

    /* Facing change means the SH rotation matrix must be rebuilt. */
    h->rot_order = -1;
    return 0;
}

DL_EXPORT int dl_set_sources(void *handle, int n_sources, const float *xyz,
                             const float *gains)
{
    dl_handle_t *h = (dl_handle_t *)handle;
    if (!h || n_sources < 0 || n_sources > DL_MAX_SOURCES) return -1;
    if (n_sources > 0 && !xyz) return -1;

    h->n_sources = n_sources;
    ambi_roomsim_setNumSources(h->hAmbi, n_sources > 0 ? n_sources : 1);

    for (int i = 0; i < n_sources; i++) {
        h->src_pos[i][0] = xyz[i * 3 + 0];
        h->src_pos[i][1] = xyz[i * 3 + 1];
        h->src_pos[i][2] = xyz[i * 3 + 2];
        h->src_gain[i] = gains ? gains[i] : 1.0f;

        /* SAF clamps into the room itself. */
        ambi_roomsim_setSourceX(h->hAmbi, i, h->src_pos[i][0]);
        ambi_roomsim_setSourceY(h->hAmbi, i, h->src_pos[i][1]);
        ambi_roomsim_setSourceZ(h->hAmbi, i, h->src_pos[i][2]);
    }

    return 0;
}

DL_EXPORT int dl_set_reflections(void *handle, int order, int enabled)
{
    dl_handle_t *h = (dl_handle_t *)handle;
    if (!h) return -1;

    h->reflection_order = order;
    h->enable_reflections = enabled ? 1 : 0;

    ambi_roomsim_setMaxReflectionOrder(h->hAmbi, order);
    ambi_roomsim_setEnableIMSflag(h->hAmbi, h->enable_reflections);
    return 0;
}

DL_EXPORT int dl_set_listener_facing(void *handle, float facing_deg)
{
    dl_handle_t *h = (dl_handle_t *)handle;
    if (!h) return -1;

    h->listener_facing_deg = facing_deg;
    h->rot_order = -1;
    return 0;
}

DL_EXPORT int dl_get_frame_size(void *handle)
{
    dl_handle_t *h = (dl_handle_t *)handle;
    return h ? h->frame_size : 0;
}

DL_EXPORT int dl_get_n_sh(void *handle)
{
    dl_handle_t *h = (dl_handle_t *)handle;
    return h ? h->n_sh : 0;
}

DL_EXPORT int dl_get_processing_delay(void *handle)
{
    dl_handle_t *h = (dl_handle_t *)handle;
    if (!h) return 0;
    return ambi_roomsim_getProcessingDelay();
}

/* -------------------------------------------------------------- processing */

/* Rotate an SH buffer by the listener yaw (in place). Buffer is n_sh x frames. */
static void dl_rotate_sh(dl_handle_t *h, float *buf)
{
    const int n = h->n_sh;
    float *tmp;

    if (n <= 1) return;

    tmp = (float *)calloc((size_t)n, sizeof(float));
    if (!tmp) return;

    /* out[i] = sum_j rot[i][j] * in[j] */
    for (int f = 0; f < h->frame_size; f++) {
        for (int i = 0; i < n; i++) {
            float acc = 0.0f;
            for (int j = 0; j < n; j++)
                acc += h->rot_mtx[i * n + j] * buf[j * h->frame_size + f];
            tmp[i] = acc;
        }
        for (int i = 0; i < n; i++)
            buf[i * h->frame_size + f] = tmp[i];
    }

    free(tmp);
}

/* Process exactly one internal frame. `in_frame` is n_sources x frame_size
   (channel-major, contiguous), `out_sh` is n_sh x frame_size.

   SAF takes both inputs and outputs as arrays of per-channel pointers, so
   build the pointer arrays here from the contiguous staging buffer. */
static int dl_process_frame(dl_handle_t *h, const float *in_frame,
                            float *out_sh)
{
    const float *inputs[DL_MAX_SOURCES];
    float *outputs[DL_MAX_SH];

    if (h->n_sources <= 0) {
        /* Nothing to render: emit a silent frame. */
        memset(out_sh, 0, sizeof(float) * (size_t)h->n_sh * (size_t)h->frame_size);
        return 0;
    }

    for (int i = 0; i < h->n_sources; i++)
        inputs[i] = in_frame + (size_t)i * h->frame_size;

    for (int c = 0; c < h->n_sh; c++)
        outputs[c] = out_sh + (size_t)c * h->frame_size;

    ambi_roomsim_process(h->hAmbi,
                         (const float *const *)inputs,
                         (float *const *)outputs,
                         h->n_sources,
                         h->n_sh,
                         h->frame_size);

    dl_update_rotation(h);
    dl_rotate_sh(h, out_sh);

    return 0;
}

/* Process `n_samples` from `inputs` (n_sources x n_samples, mono per source)
 * into `sh_out` (n_sh x n_samples).
 *
 * Buffering is handled here: the caller may pass any block size, and SAF
 * always sees its own fixed frame size. Leftover samples from the final
 * partial frame are dropped — callers that need the tail must zero-pad.
 */
DL_EXPORT int dl_process(void *handle, int n_samples, const float *inputs,
                         float *sh_out)
{
    dl_handle_t *h = (dl_handle_t *)handle;
    if (!h || !h->prepared) return -1;
    if (n_samples <= 0) return 0;
    if (!inputs || !sh_out) return -1;

    const int fs = h->frame_size;
    const int n = h->n_sh;
    int done = 0;

    while (done < n_samples) {
        const int chunk = (n_samples - done < fs) ? (n_samples - done) : fs;

        /* Gather this chunk into the source-major staging buffer. */
        for (int s = 0; s < h->n_sources; s++) {
            const float *src = inputs + (size_t)s * n_samples + done;
            float gain = h->src_gain[s];
            float *dst = h->in_frames + (size_t)s * fs;

            if (chunk == fs) {
                for (int i = 0; i < fs; i++) dst[i] = src[i] * gain;
            } else {
                for (int i = 0; i < chunk; i++) dst[i] = src[i] * gain;
                for (int i = chunk; i < fs; i++) dst[i] = 0.0f;
            }
        }

        if (dl_process_frame(h, h->in_frames, h->sh_frames) != 0)
            return -1;

        /* Copy out the valid part of the SH frame. */
        for (int c = 0; c < n; c++) {
            const float *src = h->sh_frames + (size_t)c * fs;
            float *dst = sh_out + (size_t)c * n_samples + done;
            for (int i = 0; i < chunk; i++) dst[i] = src[i];
        }

        done += chunk;
    }

    return 0;
}

/* ------------------------------------------------------------------ decode */

/* Decode an SH signal (n_sh x n_samples, N3D/ACN) to stereo using a
 * max-rE-decoded virtual speaker pair at +-30 degrees.
 *
 * Returns the number of output samples written per channel.
 */
DL_EXPORT int dl_decode_stereo(void *handle, int n_samples, const float *sh,
                               float *stereo)
{
    dl_handle_t *h = (dl_handle_t *)handle;
    if (!h || !sh || !stereo) return -1;
    if (n_samples <= 0) return 0;

    const int n = h->n_sh;

    /* Two virtual speakers, azimuth in degrees, elevation 0.
     * SAF azimuth: 0 = front (+x), +90 = left (+y). */
    const float dirs[4] = { 30.0f, 0.0f, -30.0f, 0.0f };
    float dec[2 * DL_MAX_SH];

    /* SAD (hypercardioid beamformers towards each virtual speaker) is the
       robust choice for a 2-channel layout. ALLRAD needs >=3 speakers to
       build a VBAP triangle table and returns silence here; MMD is the
       pseudo-inverse and is ill-conditioned for nLS < nSH. max-rE weighting
       keeps directional loudness even when truncating to a lower order. */
    getLoudspeakerDecoderMtx((float *)dirs, 2, LOUDSPEAKER_DECODER_SAD,
                             h->order, 1, dec);

    memset(stereo, 0, sizeof(float) * 2 * (size_t)n_samples);

    for (int i = 0; i < n_samples; i++) {
        float l = 0.0f;
        float r = 0.0f;
        for (int c = 0; c < n; c++) {
            const float s = sh[(size_t)c * n_samples + i];
            l += dec[0 * n + c] * s;
            r += dec[1 * n + c] * s;
        }
        stereo[i] = l;
        stereo[n_samples + i] = r;
    }

    return 0;
}

/* Convert an in-place N3D buffer to SN3D (AmbiX). */
DL_EXPORT int dl_to_sn3d(void *handle, int n_samples, float *sh)
{
    dl_handle_t *h = (dl_handle_t *)handle;
    if (!h || !sh) return -1;
    if (n_samples <= 0) return 0;

    convertHOANormConvention(sh, h->order, n_samples,
                             HOA_NORM_N3D, HOA_NORM_SN3D);
    return 0;
}

/* ---------------------------------------------------------------- version */

DL_EXPORT int dl_abi_version(void)
{
    return 1;
}