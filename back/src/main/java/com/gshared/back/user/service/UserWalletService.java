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
import lombok.RequiredArgsConstructor;
import org.springframework.dao.DataIntegrityViolationException;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.time.Instant;
import java.util.UUID;

/**
 * 로그인한 사용자가 자기 계정에 지갑을 연결한다.
 * 지갑 서명은 오프체인이라 온체인 트랜잭션도 가스비도 발생하지 않는다.
 */
@Service
@RequiredArgsConstructor
@Transactional(readOnly = true)
public class UserWalletService {

    private final UserRepository userRepository;
    private final WalletChallengeRepository walletChallengeRepository;
    private final WalletAddressNormalizer walletAddressNormalizer;
    private final WalletLoginMessageFactory walletLoginMessageFactory;
    private final EthereumSignatureVerifier ethereumSignatureVerifier;
    private final WalletAuthProperties walletAuthProperties;

    public WalletResponse getWallet(Long userId) {
        User user = userRepository.findById(userId)
                .orElseThrow(() -> new BusinessException(UserErrorCode.USER_NOT_FOUND));
        return WalletResponse.of(user.getWalletAddress());
    }

    /**
     * 서명할 문장을 만들어 Redis 에 보관하고 사용자에게 돌려준다.
     * 이 단계에서는 DB 가 바뀌지 않는다. 서명하지 않고 이탈하면 TTL 로 사라진다.
     */
    public WalletChallengeResponse issueChallenge(Long userId, WalletChallengeRequest request) {
        String walletAddress = walletAddressNormalizer.normalize(request.walletAddress());
        validateChain(request.chainId());

        // 서명까지 다 마친 뒤에 거절당하지 않도록 미리 걸러준다. 실제 방어는 link() 와 DB unique 제약이다.
        if (userRepository.existsByWalletAddress(walletAddress)) {
            throw new BusinessException(UserErrorCode.WALLET_ALREADY_LINKED);
        }

        String challengeId = UUID.randomUUID().toString();
        Instant issuedAt = Instant.now();
        Instant expiresAt = issuedAt.plus(walletAuthProperties.challengeExpiration());

        String message = walletLoginMessageFactory.create(walletAddress, challengeId, issuedAt, expiresAt);

        walletChallengeRepository.save(
                challengeId,
                new WalletChallenge(userId, walletAddress, message,
                        walletAuthProperties.chainId(), issuedAt, expiresAt),
                walletAuthProperties.challengeExpiration()
        );

        return WalletChallengeResponse.of(challengeId, message, expiresAt);
    }

    /**
     * 서명을 검증하고 지갑 주소를 사용자에게 저장한다.
     * 이미 연결된 지갑이 있으면 새 지갑으로 교체된다.
     */
    @Transactional
    public WalletResponse link(Long userId, WalletLinkRequest request) {
        // 꺼내는 순간 삭제된다. 검증에 실패해도 이 challenge 는 다시 쓸 수 없다.
        WalletChallenge challenge = walletChallengeRepository.consume(request.challengeId())
                .orElseThrow(() -> new BusinessException(UserErrorCode.CHALLENGE_INVALID));

        // 발급받은 사람과 제출하는 사람이 같아야 한다.
        if (!challenge.userId().equals(userId)) {
            throw new BusinessException(UserErrorCode.CHALLENGE_INVALID);
        }
        // TTL 로 이미 사라지지만 시계 오차를 감안해 한 번 더 확인한다.
        if (challenge.isExpired(Instant.now())) {
            throw new BusinessException(UserErrorCode.CHALLENGE_INVALID);
        }

        // 클라이언트가 보낸 문장이 아니라 Redis 에 저장해둔 서버 생성 문장만 해시한다.
        String recovered = ethereumSignatureVerifier.recoverAddress(challenge.message(), request.signature());
        if (!recovered.equals(challenge.walletAddress())) {
            throw new BusinessException(UserErrorCode.INVALID_WALLET_SIGNATURE);
        }

        if (userRepository.existsByWalletAddress(challenge.walletAddress())) {
            throw new BusinessException(UserErrorCode.WALLET_ALREADY_LINKED);
        }

        User user = userRepository.findById(userId)
                .orElseThrow(() -> new BusinessException(UserErrorCode.USER_NOT_FOUND));
        user.linkWallet(challenge.walletAddress());

        // 위 중복 검사와 저장 사이에 다른 요청이 같은 주소를 먼저 넣었을 수 있다.
        // 마지막 방어선인 DB unique 제약을 지금 확인해 의미 있는 오류로 바꾼다.
        try {
            userRepository.flush();
        } catch (DataIntegrityViolationException e) {
            throw new BusinessException(UserErrorCode.WALLET_ALREADY_LINKED);
        }

        return WalletResponse.of(user.getWalletAddress());
    }

    @Transactional
    public void unlink(Long userId) {
        User user = userRepository.findById(userId)
                .orElseThrow(() -> new BusinessException(UserErrorCode.USER_NOT_FOUND));
        user.unlinkWallet();
    }

    private void validateChain(Long chainId) {
        if (!walletAuthProperties.chainId().equals(chainId)) {
            throw new BusinessException(UserErrorCode.CHAIN_NOT_SUPPORTED);
        }
    }
}
