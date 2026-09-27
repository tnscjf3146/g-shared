package com.gshared.back.user.domain;

import com.gshared.back.global.event.BaseTimeEntity;
import jakarta.persistence.*;
import lombok.AccessLevel;
import lombok.Getter;
import lombok.NoArgsConstructor;

@Entity
@Getter
@Table(name = "users")
@NoArgsConstructor(access = AccessLevel.PROTECTED)
public class User extends BaseTimeEntity {

    @Id
    @Column(name = "user_id")
    @GeneratedValue(strategy = GenerationType.IDENTITY)
    private Long id;

    @Column(nullable = false)
    private String email;

    @Column(nullable = false)
    private String password;

    @Column(nullable = false, length = 15)
    private String nickname;

    @Column(nullable = false)
    @Enumerated(EnumType.STRING)
    private Role role;

    // unique 제약은 실제로는 Flyway 마이그레이션이 건다. (ddl-auto: validate)
    @Column(length = 42, unique = true)
    private String walletAddress;

    private User(String email, String password, String nickname) {
        this.email = email;
        this.password = password;
        this.nickname = nickname;
        this.role = Role.USER;
    }

    public static User createUser(String email, String password, String nickname) {
        return new User(email, password, nickname);
    }

    // 정규화된 주소만 넘어온다는 전제다. 형식 검증과 소문자 변환은 서비스 계층이 담당한다.
    public void linkWallet(String walletAddress) {
        this.walletAddress = walletAddress;
    }

    public void unlinkWallet() {
        this.walletAddress = null;
    }

    /** 표시 이름 변경. 형식 검증은 요청 DTO 가 한다. */
    public void changeNickname(String nickname) {
        this.nickname = nickname;
    }
}
