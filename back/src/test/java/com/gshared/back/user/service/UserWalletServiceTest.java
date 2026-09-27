package com.gshared.back.user.service;

import com.gshared.back.global.exception.BusinessException;
import com.gshared.back.global.wallet.EthereumSignatureVerifier;
import com.gshared.back.global.wallet.WalletAddressNormalizer;
import com.gshared.back.global.wallet.WalletAuthProperties;
import com.gshared.back.global.wallet.WalletLoginMessageFactory;
import com.gshared.back.user.domain.User;
import com.gshared.back.user.domain.WalletChallenge;
import com.gshared.back.user.dto.request.WalletChallengeRequest;
import com.gshared.back.user.dto.request.WalletLinkRequest;
import com.gshared.back.user.dto.response.WalletChallengeResponse;
import com.gshared.back.user.dto.response.WalletResponse;
import com.gshared.back.user.exception.UserErrorCode;
import com.gshared.back.user.repository.UserRepository;
import com.gshared.back.user.repository.WalletChallengeRepository;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Nested;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.ArgumentCaptor;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;
import org.springframework.dao.DataIntegrityViolationException;
import org.web3j.crypto.ECKeyPair;
import org.web3j.crypto.Sign;
import org.web3j.utils.Numeric;

import java.nio.charset.StandardCharsets;
import java.time.Duration;
import java.time.Instant;
import java.util.Optional;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.mockito.BDDMockito.eq;
import static org.mockito.BDDMockito.given;
import static org.mockito.BDDMockito.then;
import static org.mockito.BDDMockito.willThrow;

/**
 * 서명 검증·정규화·문장 생성은 실제 구현을 그대로 쓰고 DB/Redis 만 대역으로 바꾼다.
 * 덕분에 실제 개인키로 만든 서명이 흐름 전체를 통과하는지 확인할 수 있다.
 */
@ExtendWith(MockitoExtension.class)
class UserWalletServiceTest {

    // Hardhat 이 공개 문서에 싣는 테스트 전용 계정. 실제 자산이 없는 명백한 더미 키다.
    private static final String PRIVATE_KEY =
            "0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80";
    private static final String OTHER_PRIVATE_KEY =
            "0x59c6995e998f97a5a0044966f0945389dc9e86dae88c7a8412f4603b6b78690d";
    private static final String ADDRESS = "0xf39fd6e51aad88f6f4ce6ab8827279cfffb92266";
    private static final String CHECKSUM_ADDRESS = "0xf39Fd6e51aad88F6F4ce6aB8827279cffFb92266";

    private static final Long USER_ID = 42L;
    private static final Long CHAIN_ID = 31221L;
    private static final Duration TTL = Duration.ofMinutes(5);
    private static final String CHALLENGE_ID = "0f3270ab-05e1-4dda-aafd-9d68578c8c32";

    @Mock
    private UserRepository userRepository;
    @Mock
    private WalletChallengeRepository walletChallengeRepository;

    private WalletLoginMessageFactory messageFactory;
    private UserWalletService userWalletService;

    @BeforeEach
    void setUp() {
        WalletAuthProperties properties =
                new WalletAuthProperties("gshared.io", "https://gshared.io", CHAIN_ID, TTL);
        messageFactory = new WalletLoginMessageFactory(properties);
        userWalletService = new UserWalletService(
                userRepository,
                walletChallengeRepository,
                new WalletAddressNormalizer(),
                messageFactory,
                new EthereumSignatureVerifier(),
                properties
        );
    }

    @Nested
    @DisplayName("challenge 발급")
    class IssueChallenge {

        @Test
        @DisplayName("정규화된 주소와 서버가 만든 문장을 Redis 에 저장한다")
        void savesChallenge() {
            given(userRepository.existsByWalletAddress(ADDRESS)).willReturn(false);

            WalletChallengeResponse response = userWalletService.issueChallenge(
                    USER_ID, new WalletChallengeRequest(CHECKSUM_ADDRESS, CHAIN_ID));

            ArgumentCaptor<WalletChallenge> captor = ArgumentCaptor.forClass(WalletChallenge.class);
            then(walletChallengeRepository).should().save(eq(response.challengeId()), captor.capture(), eq(TTL));

            WalletChallenge saved = captor.getValue();
            assertThat(saved.userId()).isEqualTo(USER_ID);
            assertThat(saved.walletAddress()).isEqualTo(ADDRESS);       // 소문자로 정규화
            assertThat(saved.message()).isEqualTo(response.message());  // 응답과 저장본이 같은 문장
            assertThat(saved.chainId()).isEqualTo(CHAIN_ID);

            assertThat(response.challengeId()).isNotBlank();
            assertThat(response.message()).contains(ADDRESS)
                    .startsWith("gshared.io wants you to sign in");
            assertThat(response.expiresAt()).isAfter(Instant.now());
        }

        @Test
        @DisplayName("지원하지 않는 체인이면 CHAIN_NOT_SUPPORTED")
        void rejectsUnsupportedChain() {
            assertThatThrownBy(() -> userWalletService.issueChallenge(
                    USER_ID, new WalletChallengeRequest(ADDRESS, 1L)))
                    .isInstanceOf(BusinessException.class)
                    .extracting("errorCode").isEqualTo(UserErrorCode.CHAIN_NOT_SUPPORTED);

            then(walletChallengeRepository).shouldHaveNoInteractions();
        }

        @Test
        @DisplayName("주소 형식이 잘못되면 INVALID_WALLET_ADDRESS")
        void rejectsMalformedAddress() {
            assertThatThrownBy(() -> userWalletService.issueChallenge(
                    USER_ID, new WalletChallengeRequest("0x123", CHAIN_ID)))
                    .isInstanceOf(BusinessException.class)
                    .extracting("errorCode").isEqualTo(UserErrorCode.INVALID_WALLET_ADDRESS);

            then(walletChallengeRepository).shouldHaveNoInteractions();
        }

        @Test
        @DisplayName("이미 다른 계정이 쓰는 주소면 서명 전에 미리 막는다")
        void rejectsAlreadyLinkedAddress() {
            given(userRepository.existsByWalletAddress(ADDRESS)).willReturn(true);

            assertThatThrownBy(() -> userWalletService.issueChallenge(
                    USER_ID, new WalletChallengeRequest(ADDRESS, CHAIN_ID)))
                    .isInstanceOf(BusinessException.class)
                    .extracting("errorCode").isEqualTo(UserErrorCode.WALLET_ALREADY_LINKED);

            then(walletChallengeRepository).shouldHaveNoInteractions();
        }
    }

    @Nested
    @DisplayName("지갑 연결")
    class Link {

        @Test
        @DisplayName("정상 서명이면 사용자에게 지갑 주소를 저장한다")
        void linksWallet() {
            WalletChallenge challenge = challenge(USER_ID, Instant.now());
            User user = user();

            given(walletChallengeRepository.consume(CHALLENGE_ID)).willReturn(Optional.of(challenge));
            given(userRepository.existsByWalletAddress(ADDRESS)).willReturn(false);
            given(userRepository.findById(USER_ID)).willReturn(Optional.of(user));

            WalletResponse response = userWalletService.link(
                    USER_ID, new WalletLinkRequest(CHALLENGE_ID, sign(challenge.message(), PRIVATE_KEY)));

            assertThat(response.walletAddress()).isEqualTo(ADDRESS);
            assertThat(user.getWalletAddress()).isEqualTo(ADDRESS);
            then(userRepository).should().flush();
        }

        @Test
        @DisplayName("challenge 가 없으면(만료·재사용) CHALLENGE_INVALID")
        void rejectsConsumedOrExpiredChallenge() {
            given(walletChallengeRepository.consume(CHALLENGE_ID)).willReturn(Optional.empty());

            assertThatThrownBy(() -> userWalletService.link(
                    USER_ID, new WalletLinkRequest(CHALLENGE_ID, "0x" + "00".repeat(65))))
                    .isInstanceOf(BusinessException.class)
                    .extracting("errorCode").isEqualTo(UserErrorCode.CHALLENGE_INVALID);

            then(userRepository).shouldHaveNoInteractions();
        }

        @Test
        @DisplayName("남이 발급받은 challenge 는 쓸 수 없다")
        void rejectsChallengeOfAnotherUser() {
            WalletChallenge challenge = challenge(999L, Instant.now());
            given(walletChallengeRepository.consume(CHALLENGE_ID)).willReturn(Optional.of(challenge));

            assertThatThrownBy(() -> userWalletService.link(
                    USER_ID, new WalletLinkRequest(CHALLENGE_ID, sign(challenge.message(), PRIVATE_KEY))))
                    .isInstanceOf(BusinessException.class)
                    .extracting("errorCode").isEqualTo(UserErrorCode.CHALLENGE_INVALID);

            then(userRepository).shouldHaveNoInteractions();
        }

        @Test
        @DisplayName("만료 시각이 지난 challenge 는 거부한다")
        void rejectsExpiredChallenge() {
            WalletChallenge challenge = challenge(USER_ID, Instant.now().minus(Duration.ofMinutes(10)));
            given(walletChallengeRepository.consume(CHALLENGE_ID)).willReturn(Optional.of(challenge));

            assertThatThrownBy(() -> userWalletService.link(
                    USER_ID, new WalletLinkRequest(CHALLENGE_ID, sign(challenge.message(), PRIVATE_KEY))))
                    .isInstanceOf(BusinessException.class)
                    .extracting("errorCode").isEqualTo(UserErrorCode.CHALLENGE_INVALID);

            then(userRepository).shouldHaveNoInteractions();
        }

        @Test
        @DisplayName("다른 개인키로 서명하면 INVALID_WALLET_SIGNATURE")
        void rejectsSignatureFromAnotherKey() {
            WalletChallenge challenge = challenge(USER_ID, Instant.now());
            given(walletChallengeRepository.consume(CHALLENGE_ID)).willReturn(Optional.of(challenge));

            assertThatThrownBy(() -> userWalletService.link(
                    USER_ID, new WalletLinkRequest(CHALLENGE_ID, sign(challenge.message(), OTHER_PRIVATE_KEY))))
                    .isInstanceOf(BusinessException.class)
                    .extracting("errorCode").isEqualTo(UserErrorCode.INVALID_WALLET_SIGNATURE);

            then(userRepository).shouldHaveNoInteractions();
        }

        @Test
        @DisplayName("서명하는 동안 다른 사람이 먼저 등록했으면 WALLET_ALREADY_LINKED")
        void rejectsAddressLinkedMeanwhile() {
            WalletChallenge challenge = challenge(USER_ID, Instant.now());
            given(walletChallengeRepository.consume(CHALLENGE_ID)).willReturn(Optional.of(challenge));
            given(userRepository.existsByWalletAddress(ADDRESS)).willReturn(true);

            assertThatThrownBy(() -> userWalletService.link(
                    USER_ID, new WalletLinkRequest(CHALLENGE_ID, sign(challenge.message(), PRIVATE_KEY))))
                    .isInstanceOf(BusinessException.class)
                    .extracting("errorCode").isEqualTo(UserErrorCode.WALLET_ALREADY_LINKED);
        }

        @Test
        @DisplayName("DB unique 제약에 걸리면 WALLET_ALREADY_LINKED 로 바꿔 알린다")
        void translatesUniqueConstraintViolation() {
            WalletChallenge challenge = challenge(USER_ID, Instant.now());
            given(walletChallengeRepository.consume(CHALLENGE_ID)).willReturn(Optional.of(challenge));
            given(userRepository.existsByWalletAddress(ADDRESS)).willReturn(false);
            given(userRepository.findById(USER_ID)).willReturn(Optional.of(user()));
            willThrow(new DataIntegrityViolationException("duplicate")).given(userRepository).flush();

            assertThatThrownBy(() -> userWalletService.link(
                    USER_ID, new WalletLinkRequest(CHALLENGE_ID, sign(challenge.message(), PRIVATE_KEY))))
                    .isInstanceOf(BusinessException.class)
                    .extracting("errorCode").isEqualTo(UserErrorCode.WALLET_ALREADY_LINKED);
        }
    }

    @Test
    @DisplayName("조회: 미연결이면 null, 연결 후엔 저장된 주소를 돌려준다")
    void getsWallet() {
        User user = user();
        given(userRepository.findById(USER_ID)).willReturn(Optional.of(user));

        assertThat(userWalletService.getWallet(USER_ID).walletAddress()).isNull();

        user.linkWallet(ADDRESS);
        assertThat(userWalletService.getWallet(USER_ID).walletAddress()).isEqualTo(ADDRESS);
    }

    @Test
    @DisplayName("연결 해제하면 지갑 주소가 비워진다")
    void unlinksWallet() {
        User user = user();
        user.linkWallet(ADDRESS);
        given(userRepository.findById(USER_ID)).willReturn(Optional.of(user));

        userWalletService.unlink(USER_ID);

        assertThat(user.getWalletAddress()).isNull();
    }

    private User user() {
        return User.createUser("user@gshared.io", "encoded", "테스터");
    }

    private WalletChallenge challenge(Long userId, Instant issuedAt) {
        Instant expiresAt = issuedAt.plus(TTL);
        String message = messageFactory.create(ADDRESS, CHALLENGE_ID, issuedAt, expiresAt);
        return new WalletChallenge(userId, ADDRESS, message, CHAIN_ID, issuedAt, expiresAt);
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
