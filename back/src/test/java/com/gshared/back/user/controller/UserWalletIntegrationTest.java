package com.gshared.back.user.controller;

import com.gshared.back.global.security.jwt.JwtProvider;
import com.gshared.back.global.wallet.WalletAuthProperties;
import com.gshared.back.user.domain.User;
import com.gshared.back.user.dto.request.WalletChallengeRequest;
import com.gshared.back.user.dto.request.WalletLinkRequest;
import com.gshared.back.user.repository.UserRepository;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.webmvc.test.autoconfigure.AutoConfigureMockMvc;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.http.HttpHeaders;
import org.springframework.http.MediaType;
import org.springframework.test.web.servlet.MockMvc;
import org.springframework.transaction.annotation.Transactional;
import org.web3j.crypto.ECKeyPair;
import org.web3j.crypto.Sign;
import org.web3j.utils.Numeric;
import tools.jackson.databind.ObjectMapper;

import java.nio.charset.StandardCharsets;
import java.util.UUID;

import static org.assertj.core.api.Assertions.assertThat;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.delete;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

/**
 * 실제 MySQL·Redis 와 보안 필터를 거쳐 지갑 연결 흐름 전체를 확인한다.
 * 테스트 트랜잭션이 롤백되므로 DB 에는 흔적이 남지 않는다.
 */
@SpringBootTest
@AutoConfigureMockMvc
@Transactional
class UserWalletIntegrationTest {

    // Hardhat 이 공개 문서에 싣는 테스트 전용 계정. 실제 자산이 없는 명백한 더미 키다.
    private static final String PRIVATE_KEY =
            "0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80";
    private static final String OTHER_PRIVATE_KEY =
            "0x59c6995e998f97a5a0044966f0945389dc9e86dae88c7a8412f4603b6b78690d";
    private static final String ADDRESS = "0xf39fd6e51aad88f6f4ce6ab8827279cfffb92266";
    private static final String CHECKSUM_ADDRESS = "0xf39Fd6e51aad88F6F4ce6aB8827279cffFb92266";

    private static final String CHALLENGE_URL = "/api/users/me/wallet/challenge";
    private static final String WALLET_URL = "/api/users/me/wallet";

    @Autowired
    private MockMvc mockMvc;
    @Autowired
    private UserRepository userRepository;
    @Autowired
    private JwtProvider jwtProvider;
    @Autowired
    private ObjectMapper objectMapper;
    @Autowired
    private WalletAuthProperties walletAuthProperties;

    private User user;
    private String accessToken;

    /** expiresAt 은 이 테스트에서 쓰지 않으므로 필요한 두 필드만 읽는다. */
    private record ChallengeView(String challengeId, String message) {
    }

    @BeforeEach
    void setUp() {
        user = userRepository.save(User.createUser(
                "wallet-" + UUID.randomUUID() + "@gshared.io", "encoded", "지갑테스터"));
        accessToken = jwtProvider.createAccessToken(user.getId(), user.getRole());
    }

    @Test
    @DisplayName("challenge 를 받아 서명하면 지갑이 연결된다")
    void linksWalletThroughWholeFlow() throws Exception {
        ChallengeView challenge = issueChallenge(CHECKSUM_ADDRESS);

        assertThat(challenge.message())
                .startsWith(walletAuthProperties.domain() + " wants you to sign in")
                .contains(ADDRESS)                                     // 소문자로 정규화되어 들어감
                .contains("Chain ID: " + walletAuthProperties.chainId())
                .contains("Request ID: " + challenge.challengeId());

        mockMvc.perform(post(WALLET_URL)
                        .header(HttpHeaders.AUTHORIZATION, "Bearer " + accessToken)
                        .contentType(MediaType.APPLICATION_JSON)
                        .content(objectMapper.writeValueAsString(new WalletLinkRequest(
                                challenge.challengeId(), sign(challenge.message(), PRIVATE_KEY)))))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.walletAddress").value(ADDRESS));

        assertThat(userRepository.findById(user.getId()))
                .get()
                .extracting(User::getWalletAddress)
                .isEqualTo(ADDRESS);
        assertThat(userRepository.findByWalletAddress(ADDRESS)).isPresent();
    }

    @Test
    @DisplayName("같은 challenge 를 두 번 쓰면 두 번째는 거부된다")
    void rejectsReusedChallenge() throws Exception {
        ChallengeView challenge = issueChallenge(ADDRESS);
        String signature = sign(challenge.message(), PRIVATE_KEY);

        link(challenge.challengeId(), signature).andExpect(status().isOk());

        link(challenge.challengeId(), signature)
                .andExpect(status().isGone())
                .andExpect(jsonPath("$.code").value("CHALLENGE_INVALID"));
    }

    @Test
    @DisplayName("다른 개인키로 서명하면 연결되지 않는다")
    void rejectsSignatureFromAnotherKey() throws Exception {
        ChallengeView challenge = issueChallenge(ADDRESS);

        link(challenge.challengeId(), sign(challenge.message(), OTHER_PRIVATE_KEY))
                .andExpect(status().isUnauthorized())
                .andExpect(jsonPath("$.code").value("INVALID_WALLET_SIGNATURE"));

        assertThat(userRepository.findById(user.getId()))
                .get()
                .extracting(User::getWalletAddress)
                .isNull();
    }

    @Test
    @DisplayName("지원하지 않는 체인이면 challenge 를 주지 않는다")
    void rejectsUnsupportedChain() throws Exception {
        mockMvc.perform(post(CHALLENGE_URL)
                        .header(HttpHeaders.AUTHORIZATION, "Bearer " + accessToken)
                        .contentType(MediaType.APPLICATION_JSON)
                        .content(objectMapper.writeValueAsString(
                                new WalletChallengeRequest(ADDRESS, 1L))))
                .andExpect(status().isBadRequest())
                .andExpect(jsonPath("$.code").value("CHAIN_NOT_SUPPORTED"));
    }

    @Test
    @DisplayName("주소 형식이 잘못되면 challenge 를 주지 않는다")
    void rejectsMalformedAddress() throws Exception {
        mockMvc.perform(post(CHALLENGE_URL)
                        .header(HttpHeaders.AUTHORIZATION, "Bearer " + accessToken)
                        .contentType(MediaType.APPLICATION_JSON)
                        .content(objectMapper.writeValueAsString(
                                new WalletChallengeRequest("0x123", walletAuthProperties.chainId()))))
                .andExpect(status().isBadRequest())
                .andExpect(jsonPath("$.code").value("INVALID_WALLET_ADDRESS"));
    }

    @Test
    @DisplayName("인증 없이 호출하면 401")
    void requiresAuthentication() throws Exception {
        mockMvc.perform(post(CHALLENGE_URL)
                        .contentType(MediaType.APPLICATION_JSON)
                        .content(objectMapper.writeValueAsString(
                                new WalletChallengeRequest(ADDRESS, walletAuthProperties.chainId()))))
                .andExpect(status().isUnauthorized());

        mockMvc.perform(delete(WALLET_URL))
                .andExpect(status().isUnauthorized());
    }

    @Test
    @DisplayName("연결을 해제하면 지갑 주소가 비워진다")
    void unlinksWallet() throws Exception {
        ChallengeView challenge = issueChallenge(ADDRESS);
        link(challenge.challengeId(), sign(challenge.message(), PRIVATE_KEY))
                .andExpect(status().isOk());

        mockMvc.perform(delete(WALLET_URL)
                        .header(HttpHeaders.AUTHORIZATION, "Bearer " + accessToken))
                .andExpect(status().isNoContent());

        assertThat(userRepository.findById(user.getId()))
                .get()
                .extracting(User::getWalletAddress)
                .isNull();
    }

    private ChallengeView issueChallenge(String walletAddress) throws Exception {
        String body = mockMvc.perform(post(CHALLENGE_URL)
                        .header(HttpHeaders.AUTHORIZATION, "Bearer " + accessToken)
                        .contentType(MediaType.APPLICATION_JSON)
                        .content(objectMapper.writeValueAsString(new WalletChallengeRequest(
                                walletAddress, walletAuthProperties.chainId()))))
                .andExpect(status().isOk())
                .andReturn()
                .getResponse()
                .getContentAsString(StandardCharsets.UTF_8);

        return objectMapper.readValue(body, ChallengeView.class);
    }

    private org.springframework.test.web.servlet.ResultActions link(String challengeId, String signature)
            throws Exception {
        return mockMvc.perform(post(WALLET_URL)
                .header(HttpHeaders.AUTHORIZATION, "Bearer " + accessToken)
                .contentType(MediaType.APPLICATION_JSON)
                .content(objectMapper.writeValueAsString(new WalletLinkRequest(challengeId, signature))));
    }

    /** MetaMask 의 personal_sign 과 같은 방식으로 서명해 65바이트 16진수 문자열을 만든다. */
    private String sign(String message, String privateKey) {
        ECKeyPair keyPair = ECKeyPair.create(Numeric.toBigInt(privateKey));
        Sign.SignatureData data =
                Sign.signPrefixedMessage(message.getBytes(StandardCharsets.UTF_8), keyPair);

        byte[] raw = new byte[65];
        System.arraycopy(data.getR(), 0, raw, 0, 32);
        System.arraycopy(data.getS(), 0, raw, 32, 32);
        raw[64] = data.getV()[0];
        return Numeric.toHexString(raw);
    }
}
