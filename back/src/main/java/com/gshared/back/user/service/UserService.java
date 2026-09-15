package com.gshared.back.user.service;

import com.gshared.back.global.exception.BusinessException;
import com.gshared.back.user.domain.User;
import com.gshared.back.user.dto.response.MeResponse;
import com.gshared.back.user.exception.UserErrorCode;
import com.gshared.back.user.repository.UserRepository;
import lombok.RequiredArgsConstructor;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

/** 로그인한 사용자의 계정 정보. 대상은 요청 본문이 아니라 인증 정보에서 온다. */
@Service
@RequiredArgsConstructor
@Transactional(readOnly = true)
public class UserService {

    private final UserRepository userRepository;

    public MeResponse getMe(Long userId) {
        return MeResponse.from(findUser(userId));
    }

    /** 형식 검증(2~15자·영문·숫자·한글)은 DTO 가 했다. 중복 허용 — 닉네임은 식별자가 아니라 표시 이름이다. */
    @Transactional
    public void updateNickname(Long userId, String nickname) {
        findUser(userId).changeNickname(nickname);
    }

    private User findUser(Long userId) {
        return userRepository.findById(userId)
                .orElseThrow(() -> new BusinessException(UserErrorCode.USER_NOT_FOUND));
    }
}
