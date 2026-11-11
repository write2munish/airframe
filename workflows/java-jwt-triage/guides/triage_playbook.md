# auth0/java-jwt Bug Triage Playbook (Day-1 Baseline)

Triage incoming `auth0/java-jwt` bug reports to the exact responsible subsystem and Java source file.
Before emitting `BugTriageReport`, call `inspect_java_jwt_package` to confirm the target path exists.

## Routing Rules
- Rule `crypto-ecdsa`: terms [`es256`, `es384`, `es512`, `ecdsa`, `r=0`] -> `algorithms` | `lib/src/main/java/com/auth0/jwt/algorithms/ECDSAAlgorithm.java`
- Rule `claim-check`: terms [`exp`, `aud`, `claim`] -> `core-verifier` | `lib/src/main/java/com/auth0/jwt/JWTVerifier.java`
- Rule `decode-default`: terms [`decode`, `base64`, `jwt`] -> `core-token` | `lib/src/main/java/com/auth0/jwt/JWTDecoder.java`
