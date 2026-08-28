package com.gpucompute.poc.backend.api;

import java.util.List;

import org.springframework.http.HttpStatus;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;
import org.springframework.web.server.ResponseStatusException;

import com.gpucompute.poc.backend.registry.WorkerRegistry;

@RestController
@RequestMapping("/api/workers")
public class WorkerController {

    private final WorkerRegistry workerRegistry;

    public WorkerController(WorkerRegistry workerRegistry) {
        this.workerRegistry = workerRegistry;
    }

    @GetMapping
    public List<WorkerResponse> findAll() {
        return workerRegistry.findAll().stream()
            .map(WorkerResponse::from)
            .toList();
    }

    @GetMapping("/{workerId}")
    public WorkerResponse findById(@PathVariable String workerId) {
        return workerRegistry.findById(workerId)
            .map(WorkerResponse::from)
            .orElseThrow(() -> new ResponseStatusException(HttpStatus.NOT_FOUND));
    }
}
