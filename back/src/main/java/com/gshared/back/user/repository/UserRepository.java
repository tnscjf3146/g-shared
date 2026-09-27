package com.gshared.back.user.repository;

import com.gshared.back.user.domain.User;
import org.springframework.data.jpa.repository.JpaRepository;

import java.util.Optional;

public interface UserRepository extends JpaRepository<User, Long> {

    Optional<User> findByEmail(String email);

    boolean existsByEmail(String email);

    Optional<User> findByWalletAddress(String walletAddress);

    boolean existsByWalletAddress(String walletAddress);
}
