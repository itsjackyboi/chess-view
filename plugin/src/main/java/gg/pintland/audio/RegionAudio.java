package gg.pintland.audio;

/** Immutable region → audio mapping loaded from regions.yml. */
public final class RegionAudio {
    public final String url;
    public final int volume;

    public RegionAudio(String url, int volume) {
        this.url = url == null ? "" : url;
        this.volume = volume;
    }

    /** True when this mapping means "silence" (no url). */
    public boolean isSilent() {
        return url.isEmpty();
    }
}
