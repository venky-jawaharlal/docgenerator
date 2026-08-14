package com.example.catalog;

import org.springframework.web.bind.annotation.*;
import org.springframework.security.access.prepost.PreAuthorize;
import javax.servlet.http.HttpSession;
import java.util.List;

@RestController
@RequestMapping("/api/products")
public class ProductController {

    @GetMapping
    public List<Product> listProducts(@RequestParam(required = false) String category,
                                       @RequestParam(defaultValue = "0") int page) {
        return List.of();
    }

    @GetMapping("/{id}")
    public Product getProduct(@PathVariable Long id) {
        return new Product();
    }

    @PostMapping
    @PreAuthorize("hasRole('ADMIN')")
    public Product createProduct(@RequestBody CreateProductRequest request) {
        return new Product();
    }

    @PutMapping("/{id}")
    @PreAuthorize("hasRole('ADMIN')")
    public Product updateProduct(@PathVariable Long id,
                                 @RequestBody CreateProductRequest request,
                                 @RequestHeader("X-Request-Id") String requestId) {
        return new Product();
    }

    @DeleteMapping("/{id}")
    @PreAuthorize("hasRole('ADMIN')")
    public void deleteProduct(@PathVariable Long id) {
    }

    @GetMapping("/cart")
    public Cart currentCart(HttpSession session) {
        return new Cart();
    }
}
