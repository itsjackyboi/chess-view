package gg.pintland.audio;

import java.nio.charset.StandardCharsets;
import java.util.Base64;
import javax.crypto.Mac;
import javax.crypto.spec.SecretKeySpec;

/**
 * Signs player UUIDs into client tokens of the form {@code <uuid>.<base64url-hmac>}.
 *
 * <p>This MUST stay byte-identical to the relay's {@code src/tokens.js}:
 * HMAC-SHA256 over the UUID string, keyed by the shared token secret, encoded
 * as unpadded base64url. If either side changes, join links stop verifying.
 */
public final class TokenSigner {

    private final String secret;

    public TokenSigner(String secret) {
        this.secret = secret;
    }

    /** Returns the unpadded base64url HMAC-SHA256 of {@code uuid}. */
    public String signature(String uuid) {
        try {
            Mac mac = Mac.getInstance("HmacSHA256");
            mac.init(new SecretKeySpec(secret.getBytes(StandardCharsets.UTF_8), "HmacSHA256"));
            byte[] digest = mac.doFinal(uuid.getBytes(StandardCharsets.UTF_8));
            return Base64.getUrlEncoder().withoutPadding().encodeToString(digest);
        } catch (Exception e) {
            throw new IllegalStateException("Failed to sign token", e);
        }
    }

    /** Returns a full signed token {@code <uuid>.<sig>}. */
    public String sign(String uuid) {
        return uuid + "." + signature(uuid);
    }
}
